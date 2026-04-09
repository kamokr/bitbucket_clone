Bitbucket Workspace Bare Clone Tool
===================================

Purpose
-------
This script clones all repositories from one or more Bitbucket Cloud workspaces
as bare repositories. It is useful when preparing repository migration to local
storage (for example NAS) or to another Git hosting service.

Script file: clone.py

How it works
------------
1. Connects to Bitbucket API v2 using Basic Authentication:
   - Bitbucket username
   - Bitbucket API token (app password)
2. Calls the repositories endpoint for each workspace:
   https://api.bitbucket.org/2.0/repositories/{workspace}
3. Follows paginated responses by reading the next field until all pages are read.
4. Selects HTTPS clone URL for each repository.
5. Runs:
   `git clone --bare <repo-url> <destination>`

Create an API token
-------------------

https://support.atlassian.com/bitbucket-cloud/docs/create-an-api-token/

Repository destination layout
-----------------------------
By default repositories are created in this structure:

`<DESTINATION_ROOT>/<workspace>/<repo>.git`

Prerequisites
-------------
- Python 3.10+
- git available in PATH
- Python package: requests

Install dependency in this workspace virtual environment:

`.venv/bin/python -m pip install requests`

Configuration
-------------
Open clone.py and edit constants in the Configuration section:

- BITBUCKET_USERNAME
- BITBUCKET_API_TOKEN
- WORKSPACES
- DESTINATION_ROOT
- PAGELEN
- REQUEST_TIMEOUT_SECONDS
- DRY_RUN
- OVERWRITE_EXISTING
- REPORT_JSON_PATH

Important notes:
- Keep credentials private.
- Do not commit real tokens to version control.

Run
---
From the project folder:

`.venv/bin/python clone.py`

Behavior flags
--------------
- `DRY_RUN = True`  
  Discovers repositories and prints planned actions without cloning.

- `OVERWRITE_EXISTING = False`  
  If destination already exists, repository is skipped.

- `OVERWRITE_EXISTING = True`  
  Existing destination is removed and cloned again.

- `REPORT_JSON_PATH = Path("./report.json")`  
  Writes a JSON summary report file.

Output summary
--------------
At the end the script prints:
- discovered jobs
- cloned
- skipped
- failed

Exit code
---------
- 0: no clone failures
- 1: one or more repositories failed to clone
- 2: authentication/authorization/resource errors from API checks

Troubleshooting
---------------
1) Authentication failed (401)
   - Verify username and API token.
   - Ensure token is active.

2) Access forbidden (403)
   - Token does not have enough permissions for workspace repositories.

3) Resource not found (404)
   - Workspace slug may be incorrect.

4) Import errors for requests
   - Install package in the same Python environment used to run clone.py.

5) git not found
   - Install git and ensure it is in PATH.
