# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ScanSession(models.Model):
    """
    Session de scan : regroupe un panier de produits scannés
    destiné à générer une facture client.
    """
    _name = 'scan.session'
    _description = 'Session de scan produits'
    _order = 'create_date desc'
    _rec_name = 'name'

    # ── Identification ──────────────────────────────────────────────────────

    name = fields.Char(
        string='Référence',
        required=True,
        copy=False,
        default=lambda self: _('Nouvelle session'),
    )
    date = fields.Date(
        string='Date',
        default=fields.Date.today,
        required=True,
    )

    # ── Relations ───────────────────────────────────────────────────────────

    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string='Client',
        required=True,
        domain=[('customer_rank', '>', 0)],
    )
    line_ids = fields.One2many(
        comodel_name='scan.session.line',
        inverse_name='session_id',
        string='Lignes du panier',
    )
    invoice_id = fields.Many2one(
        comodel_name='account.move',
        string='Facture générée',
        readonly=True,
        copy=False,
    )

    # ── Montants ────────────────────────────────────────────────────────────

    currency_id = fields.Many2one(
        comodel_name='res.currency',
        string='Devise',
        default=lambda self: self.env.company.currency_id,
    )
    amount_total = fields.Monetary(
        string='Total',
        compute='_compute_amount_total',
        store=True,
        currency_field='currency_id',
    )

    # ── Statut ──────────────────────────────────────────────────────────────

    state = fields.Selection(
        selection=[
            ('draft', 'Brouillon'),
            ('invoiced', 'Facturé'),
        ],
        string='État',
        default='draft',
        tracking=True,
    )

    # ── Champ technique pour le widget de scan ───────────────────────────────

    barcode_input = fields.Char(
        string='Code-barres',
        store=False,
        help='Champ réservé au widget de scan. Non enregistré en base.',
    )

    notes = fields.Text(
        string='Notes internes',
        help='Note interne, non imprimée sur la facture.',
    )

    # ── Computed ────────────────────────────────────────────────────────────

    @api.depends('line_ids.subtotal')
    def _compute_amount_total(self):
        for session in self:
            session.amount_total = sum(session.line_ids.mapped('subtotal'))

    # ── ORM ─────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nouvelle session')) == _('Nouvelle session'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('scan.session')
                    or _('Nouvelle session')
                )
        return super().create(vals_list)

    # ── Actions métier ───────────────────────────────────────────────────────

    def action_add_by_barcode(self, barcode):
        """
        Recherche un produit par code-barres et l'ajoute au panier.

        Règles appliquées (depuis product.template) :
          - x_scan_enabled=False → produit refusé
          - x_scan_price_override > 0 → prix de scan prioritaire sur lst_price
          - x_scan_note → message renvoyé au widget pour notification

        Si le produit existe déjà dans le panier, incrémente la quantité.
        Retourne un dict {success, product_name, quantity, note} ou {error}.
        """
        self.ensure_one()

        if self.state == 'invoiced':
            return {'error': "Cette session est déjà facturée."}

        if not barcode or not barcode.strip():
            return {'error': "Code-barres vide."}

        barcode = barcode.strip()
        product = self.env['product.product'].search(
            [('barcode', '=', barcode)], limit=1
        )

        if not product:
            return {'error': f"Aucun produit trouvé pour le code : {barcode}"}

        # Vérification du flag "scannable" sur product.template
        if not product.product_tmpl_id.x_scan_enabled:
            return {
                'error': (
                    f"« {product.display_name} » est désactivé pour le scan. "
                    "Activez-le dans la fiche produit (onglet Scan)."
                )
            }

        # Prix effectif : override > 0 sinon prix de vente standard
        tmpl = product.product_tmpl_id
        price = tmpl.x_scan_price_override if tmpl.x_scan_price_override > 0 else product.lst_price

        existing = self.line_ids.filtered(lambda l: l.product_id.id == product.id)
        if existing:
            existing[0].quantity += 1
            new_qty = existing[0].quantity
        else:
            self.env['scan.session.line'].create({
                'session_id': self.id,
                'product_id': product.id,
                'quantity': 1.0,
                'price_unit': price,
            })
            new_qty = 1

        return {
            'success': True,
            'product_name': product.display_name,
            'quantity': new_qty,
            'note': tmpl.x_scan_note or '',   # affiché en notification si renseigné
        }

    def action_create_invoice(self):
        """
        Crée une facture client (out_invoice) depuis les lignes du panier.
        Redirige vers la facture créée.
        """
        self.ensure_one()

        if not self.line_ids:
            raise UserError(_("Le panier est vide. Ajoutez des produits avant de facturer."))

        if self.state == 'invoiced':
            raise UserError(_("Une facture a déjà été générée pour cette session."))

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': self.date,
            'narration': self.notes or '',
            'invoice_line_ids': [
                (0, 0, {
                    'product_id': line.product_id.id,
                    'quantity': line.quantity,
                    'price_unit': line.price_unit,
                })
                for line in self.line_ids
            ],
        })

        self.write({
            'invoice_id': invoice.id,
            'state': 'invoiced',
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Facture'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_invoice(self):
        """Ouvre la facture générée."""
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_("Aucune facture n'a encore été générée."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Facture'),
            'res_model': 'account.move',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print_invoice(self):
        """Imprime la facture en PDF (rapport standard Odoo)."""
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_("Aucune facture. Cliquez sur 'Valider' d'abord."))
        return (
            self.env['ir.actions.report']
            ._get_report_from_name('account.report_invoice')
            .report_action(self.invoice_id)
        )

    def action_reset_draft(self):
        """Remet en brouillon si la facture a été supprimée."""
        for session in self:
            if not session.invoice_id:
                session.state = 'draft'


class ScanSessionLine(models.Model):
    """
    Ligne de panier : un produit avec sa quantité et son prix
    dans une session de scan.
    """
    _name = 'scan.session.line'
    _description = 'Ligne de panier - Session scan'
    _order = 'sequence, id'

    # ── Relations ───────────────────────────────────────────────────────────

    session_id = fields.Many2one(
        comodel_name='scan.session',
        string='Session',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(
        string='Ordre',
        default=10,
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Produit',
        required=True,
        domain=[('sale_ok', '=', True)],
    )

    # ── Champs produit ───────────────────────────────────────────────────────

    barcode = fields.Char(
        related='product_id.barcode',
        string='Code-barres',
        readonly=True,
    )

    # ── Quantité & Prix ──────────────────────────────────────────────────────

    quantity = fields.Float(
        string='Quantité',
        default=1.0,
        digits='Product Unit of Measure',
        required=True,
    )
    price_unit = fields.Float(
        string='Prix unitaire',
        digits='Product Price',
        required=True,
    )
    subtotal = fields.Float(
        string='Sous-total',
        compute='_compute_subtotal',
        store=True,
        digits='Account',
    )

    # ── Computed ─────────────────────────────────────────────────────────────

    @api.depends('quantity', 'price_unit')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.price_unit

    # ── Onchange ─────────────────────────────────────────────────────────────

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.price_unit = self.product_id.lst_price
