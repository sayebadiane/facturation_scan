# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductTemplate(models.Model):
    """
    Extension de product.template pour le module de scan.
    Ajoute des champs de configuration spécifiques au scan barcode.
    """
    _inherit = 'product.template'

    # ── Champs scan ──────────────────────────────────────────────────────────

    x_scan_enabled = fields.Boolean(
        string='Scannable',
        default=True,
        help=(
            'Si décoché, ce produit sera refusé lors du scan '
            'dans une session de facturation.'
        ),
    )

    x_scan_price_override = fields.Float(
        string='Prix de scan',
        digits='Product Price',
        default=0.0,
        help=(
            'Prix unitaire appliqué lors du scan. '
            'Si 0, le prix de vente standard (lst_price) est utilisé.'
        ),
    )

    x_scan_note = fields.Char(
        string='Note scan',
        help=(
            'Message court affiché en notification lors du scan de ce produit '
            '(ex : "Fragile", "Promo -10%", "Rupture imminente").'
        ),
    )

    # ── Computed helpers ─────────────────────────────────────────────────────

    @api.depends('x_scan_price_override', 'list_price')
    def _compute_effective_scan_price(self):
        for tmpl in self:
            tmpl.x_effective_scan_price = (
                tmpl.x_scan_price_override
                if tmpl.x_scan_price_override > 0
                else tmpl.list_price
            )

    x_effective_scan_price = fields.Float(
        string='Prix effectif scan',
        compute='_compute_effective_scan_price',
        digits='Product Price',
        store=False,
        help='Prix réellement utilisé lors du scan (override ou list_price).',
    )
