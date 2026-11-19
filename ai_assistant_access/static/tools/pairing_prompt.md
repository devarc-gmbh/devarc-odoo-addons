Connect this machine to my Odoo as my personal AI assistant with ${access_mode} access.

1. Check that Python 3.10 or newer is available (`python --version`). If it is missing, tell me what to install and stop.
2. Download ${tool_url} and ${requirements_url} into one folder, then run `pip install -r requirements.txt` there.
3. Run `python odoo_ai_mcp.py pair --url ${odoo_url} --db ${db} --code=${code}`. The pairing code works once and expires in ${ttl_minutes} minutes. The script stores the credentials file itself and prints its path and an `.mcp.json` block. Never open, print or copy the credentials file.
4. Add the printed block to `.mcp.json` of this project and the printed assistant instructions to `CLAUDE.md`.
5. Tell me where the credentials file was saved, ask me to restart Claude Code, and after the restart call the `odoo_hello` tool.
