Output demo.gif

Set FontSize 20
Set Width 1200
Set Height 640
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set Padding 20

Type "export ODOO_URL=https://odoo.example.com"
Enter
Type "export ODOO_DB=mycompany"
Enter
Type "export ODOO_AI_KEY=***"
Enter
Sleep 1s

Type "# the assistant announces itself to the owner"
Enter
Type "curl -s -X POST $ODOO_URL/ai_assistant/v1/hello -H \"Authorization: Bearer $ODOO_AI_KEY\" -H 'Content-Type: application/json' -d \"{\\\"db\\\": \\\"$ODOO_DB\\\"}\""
Enter
Sleep 3s

Type "# read-only search under the owner's own permissions"
Enter
Type "curl -s -X POST $ODOO_URL/ai_assistant/v1/search_read -H \"Authorization: Bearer $ODOO_AI_KEY\" -H 'Content-Type: application/json' -d \"{\\\"db\\\": \\\"$ODOO_DB\\\", \\\"model\\\": \\\"crm.lead\\\", \\\"domain\\\": [], \\\"fields\\\": [\\\"name\\\", \\\"expected_revenue\\\"], \\\"limit\\\": 3}\""
Enter
Sleep 4s

Type "# writing is refused"
Enter
Type "curl -s -X POST $ODOO_URL/ai_assistant/v1/write -H \"Authorization: Bearer $ODOO_AI_KEY\" -H 'Content-Type: application/json' -d \"{\\\"db\\\": \\\"$ODOO_DB\\\"}\""
Enter
Sleep 4s
