# Small local search library

Pure Python with an in-memory positional index. Only body text is indexed;
titles are metadata. No service, files, or installation is required. Run
`python -m unittest discover -s tests`. Public functions own their result
dictionaries and must preserve all caller input. The original tests and
observe.py are protected; add new tests in separate files when useful.
