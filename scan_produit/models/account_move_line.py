# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    x_ref_commande_ligne = fields.Char(
        string='Réf. commande ligne',
        help='Référence spécifique à cette ligne de facture',
    )

    x_remise_speciale = fields.Float(
        string='Remise spéciale (%)',
        digits='Discount',
        default=0.0,
        help='Remise supplémentaire accordée sur cette ligne',
    )
