from odoo import fields, models


class AiAssistantPairing(models.AbstractModel):
    _name = "ai.assistant.pairing"
    _description = "AI Assistant - Pairing Prompt"

    id = fields.Id()
    prompt = fields.Text(
        string="Prompt",
        readonly=True,
        help="Prompt for your AI assistant with a one-time pairing code; "
        "it never contains the key.",
    )
