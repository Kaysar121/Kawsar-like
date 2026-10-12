# Kawsar Like - Visit API

`app.py` is the main file. The HTML interface is `templates/index.html`.

## Token switch
- `TOKEN_GENERATOR_ENABLED = False`: both Visit and Like read `token_<server>.json` for the selected server.
- `TOKEN_GENERATOR_ENABLED = True`: both Visit and Like generate tokens using `API_BASE_URL + /token`, save them to that same server's token JSON, then use them.
- `API_BASE_URL` is the only setting for the token API; `/token` is appended internally, matching the original app.py.
- Token generation needs valid server-specific account files.

## API endpoints
- `/visit/<SERVER>/<UID>`
- `/visit/<SERVER>/<UID>/<CUSTOM>`
- `/like/<SERVER>/<UID>`

The HTML displays HTTP status separately and prints the response JSON without adding an artificial `http_status` field. A real HTTP 500 still means the selected server has no usable token or the upstream operation failed; fill the matching `token_<server>.json` when generator is OFF, or enable generation and provide accounts when generator is ON.

## Central settings
At the top of `app.py`, edit `OB_VERSION`, `SERVER_URLS`, `API_BASE_URL`, `TOKEN_GENERATOR_ENABLED`, and performance settings. Visit and Like use the same token switch and region host mapping.
