"""Validation and owned copies of document records."""
def documents(rows):
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('document must be a dictionary')
        try:
            identity, title, body = row['id'], row['title'], row['body']
        except KeyError:
            raise ValueError('missing document field') from None
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ValueError('document id must be unique nonempty text')
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValueError('title and body must be strings')
        seen.add(identity)
        result.append({'id': identity, 'title': title, 'body': body})
    return result
