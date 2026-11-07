import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
try:
    from agent_lab.providers import resolve_model_config,load_model_config,provider_catalog
except ImportError:
    resolve_model_config=None


class ProviderConfigTests(unittest.TestCase):
    def test_named_provider_uses_own_key_namespace_and_endpoint(self):
        self.assertIsNotNone(resolve_model_config,'provider configuration is missing')
        settings=resolve_model_config({'provider':'deepseek_chat','model':'deepseek-flash'})
        self.assertEqual(settings['base_url'],'https://api.deepseek.com')
        self.assertEqual(settings['api_key_env'],'DEEPSEEK_API_KEY')
        self.assertEqual(settings['token_parameter'],'max_tokens')
        self.assertFalse(settings['parallel_parameter'])
        self.assertEqual(resolve_model_config({'provider':'siliconflow_chat','model':'user-chosen'})['api_key_env'],'SILICONFLOW_API_KEY')
        catalog=provider_catalog()
        self.assertFalse(next(row for row in catalog if row['provider']=='siliconflow_chat')['real_provider_tested'])

    def test_secret_values_and_credential_rerouting_are_rejected(self):
        self.assertIsNotNone(resolve_model_config,'provider configuration is missing')
        cases=[{'provider':'deepseek_chat','model':'m','api_key':'FAKE_SECRET'},
               {'provider':'deepseek_chat','model':'m','base_url':'https://example.invalid'},
               {'provider':'openai_chat','model':'m','api_key_env':'DEEPSEEK_API_KEY'},
               {'provider':'custom','protocol':'chat','model':'m','base_url':'https://example.invalid','api_key_env':'DEEPSEEK_API_KEY'}]
        for config in cases:
            with self.subTest(config=config),self.assertRaises(ValueError) as error:resolve_model_config(config)
            self.assertNotIn('FAKE_SECRET',str(error.exception))

    def test_custom_requires_explicit_routing_and_dialects_are_not_assumed_equal(self):
        self.assertIsNotNone(resolve_model_config,'provider configuration is missing')
        with self.assertRaises(ValueError):resolve_model_config({'provider':'custom','model':'m'})
        with self.assertRaises(ValueError):resolve_model_config({'provider':'siliconflow_chat','model':'m','thinking':'disabled'})
        with self.assertRaises(ValueError):resolve_model_config({'provider':'dashscope_chat','model':'m'})
        settings=resolve_model_config({'provider':'dashscope_chat','model':'m','base_url':'https://workspace-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'})
        self.assertEqual(settings['api_key_env'],'DASHSCOPE_API_KEY')

    def test_project_cannot_rewrite_credential_routing_configuration(self):
        self.assertIsNotNone(resolve_model_config,'provider configuration is missing')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            config={'provider':'deepseek_chat','model':'deepseek-flash'}
            outside=Path(temp)/'model.json';outside.write_text(json.dumps(config))
            self.assertEqual(load_model_config(outside,root)['model'],'deepseek-flash')
            inside=root/'model.json';inside.write_text(json.dumps(config))
            with self.assertRaises(ValueError):load_model_config(inside,root)
            link=Path(temp)/'link.json';link.symlink_to(outside)
            with self.assertRaises(ValueError):load_model_config(link,root)
            outside.write_text('{"provider":"custom","provider":"deepseek_chat","model":"m"}')
            with self.assertRaises(ValueError):load_model_config(outside,root)

    def test_invalid_json_option_types_have_sanitized_configuration_errors(self):
        for field,value in [('provider',[]),('base_url',3),('ca_bundle',[]),('stream','true')]:
            config={'provider':'custom','model':'m','base_url':'https://api.deepseek.com','api_key_env':'DEEPSEEK_API_KEY','protocol':'chat',field:value}
            with self.subTest(field=field),self.assertRaises(ValueError):resolve_model_config(config)

    def test_cli_can_use_trusted_config_and_list_presets_without_credentials(self):
        from agent_lab.__main__ import main
        from tests.dev.test_session_cli import TerminalModel
        from agent_lab.store import Store
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'tasks.sqlite'
            cfg=Path(temp)/'model.json';cfg.write_text(json.dumps({'provider':'custom','model':'configured',
                   'protocol':'chat','base_url':'https://example.invalid/v1','api_key_env':'SYNTHETIC_KEY'}))
            with patch('agent_lab.__main__.OpenAIModel',return_value=TerminalModel()),patch.dict(os.environ,{'SYNTHETIC_KEY':'fake'}),contextlib.redirect_stdout(io.StringIO()):
                code=main(['--db',str(db),'run','Task','--workspace',str(root),'--profile','local','--model-config',str(cfg),'--allow-api'])
            self.assertEqual(code,0)
            with Store(db) as store:
                self.assertEqual(store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        output=io.StringIO()
        with patch.dict(os.environ,{},clear=True),contextlib.redirect_stdout(output):
            self.assertEqual(main(['providers']),0)
        self.assertTrue(any(row['provider']=='deepseek_chat' for row in json.loads(output.getvalue())))

    def test_workspace_controlled_directory_alias_cannot_route_config_or_trust_store(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);root=base/'project';root.mkdir();external=base/'trusted';external.mkdir()
            (external/'model.json').write_text(json.dumps({'provider':'deepseek_chat','model':'m'}))
            (external/'ca.pem').write_text('synthetic trust file')
            (root/'alias').symlink_to(external,target_is_directory=True)
            with self.assertRaises(ValueError):load_model_config(root/'alias'/'model.json',root)
            cfg=external/'with-ca.json';cfg.write_text(json.dumps({'provider':'deepseek_chat','model':'m','ca_bundle':str(root/'alias'/'ca.pem')}))
            with self.assertRaises(ValueError):load_model_config(cfg,root)

    def test_explicit_deepseek_file_load_is_process_local_and_uses_shared_budget_adapter(self):
        from agent_lab.__main__ import main
        from agent_lab.budget import BudgetLedger as RealLedger
        from tests.dev.test_session_cli import TerminalModel
        class AuthorizedFakeModel(TerminalModel):
            api_key_env='DEEPSEEK_API_KEY';base_url='https://api.deepseek.com'
            model='deepseek-flash';allow_api=True;protocol='chat';token_parameter='max_tokens'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir();db=Path(temp)/'state.sqlite'
            cfg=Path(temp)/'model.json';cfg.write_text(json.dumps({'provider':'deepseek_chat','model':'deepseek-flash'}))
            ledger=RealLedger(Path(temp)/'paid.sqlite')
            def load():os.environ['DEEPSEEK_API_KEY']='synthetic-only-file-key'
            output=io.StringIO()
            with patch.dict(os.environ,{},clear=True),patch('agent_lab.__main__.OpenAIModel',return_value=AuthorizedFakeModel()),patch('agent_lab.budget.BudgetLedger.open_existing',return_value=ledger),patch('agent_lab.budget.load_first_line_key',side_effect=load),contextlib.redirect_stdout(output):
                code=main(['--db',str(db),'run','Task','--workspace',str(root),'--profile','local','--model-config',str(cfg),'--load-deepseek-key','--allow-api'])
                self.assertNotIn('DEEPSEEK_API_KEY',os.environ)
            self.assertEqual(code,0)
            self.assertEqual(len(ledger.report()['requests']),1)
            self.assertNotIn('synthetic-only-file-key',output.getvalue())

    def test_key_file_loader_is_not_called_without_explicit_cost_and_route_authorization(self):
        from agent_lab.__main__ import main
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project';root.mkdir()
            cases=[['--model','m'],['--model','m','--allow-api']]
            for extra in cases:
                with self.subTest(extra=extra),patch('agent_lab.budget.load_first_line_key') as loader,contextlib.redirect_stderr(io.StringIO()):
                    code=main(['--db',str(Path(temp)/'state.sqlite'),'run','Task','--workspace',str(root),'--load-deepseek-key']+extra)
                    self.assertEqual(code,2)
                    loader.assert_not_called()
