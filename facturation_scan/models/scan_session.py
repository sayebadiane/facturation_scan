# -*- coding: utf-8 -*-
import base64
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
    stock_picking_id = fields.Many2one(
        comodel_name='stock.picking',
        string='Bon de livraison',
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

    def _create_delivery_picking(self):
        """
        Crée et valide un bon de livraison (sortie de stock) pour les produits
        stockables/consommables de la session. Les services sont ignorés.
        Retourne le stock.picking créé, ou False si aucune ligne à livrer.
        """
        self.ensure_one()

        # Filtrer uniquement les produits qui gérent le stock (storable) ou consommable
        lines_to_deliver = self.line_ids.filtered(
            lambda l: l.product_id.type in ('product', 'consu')
        )
        if not lines_to_deliver:
            return False

        # Vérifier la disponibilité du stock avant de créer le bon de livraison
        insufficient = []
        for line in lines_to_deliver:
            available = line.product_id.qty_available
            if line.quantity > available:
                insufficient.append(
                    "• %s : commandé %.2f, disponible %.2f"
                    % (line.product_id.display_name, line.quantity, available)
                )
        if insufficient:
            raise UserError(_(
                "Stock insuffisant pour les produits suivants :\n%s\n\n"
                "Ajustez les quantités ou approvisionnez votre stock avant de facturer."
            ) % '\n'.join(insufficient))

        # Trouver le type d'opération "Livraison clients" (sortant) de la société courante
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('warehouse_id.company_id', '=', self.env.company.id),
        ], limit=1)

        if not picking_type:
            raise UserError(_(
                "Aucun type d'opération de livraison trouvé. "
                "Vérifiez la configuration de votre entrepôt."
            ))

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'partner_id': self.partner_id.id,
            'origin': self.name,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'move_ids': [
                (0, 0, {
                    'product_id': line.product_id.id,
                    'product_uom': line.product_id.uom_id.id,
                    'product_uom_qty': line.quantity,
                    'location_id': picking_type.default_location_src_id.id,
                    'location_dest_id': picking_type.default_location_dest_id.id,
                })
                for line in lines_to_deliver
            ],
        })

        picking.action_confirm()
        picking.action_assign()

        # Définir les quantités faites et valider sans créer de reliquat
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty

        picking.with_context(skip_backorder=True).button_validate()

        return picking

    def action_create_invoice(self):
        """
        Crée une facture client (out_invoice) depuis les lignes du panier
        et génère automatiquement le bon de livraison.
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

        picking = self._create_delivery_picking()

        self.write({
            'invoice_id': invoice.id,
            'stock_picking_id': picking.id if picking else False,
            'state': 'invoiced',
        })

        # Envoi automatique par email si le client a une adresse email
        email_sent = False
        if self.partner_id.email:
            template = self.env.ref(
                'account.email_template_edi_invoice',
                raise_if_not_found=False,
            )
            if template:
                # Générer le PDF de la facture et le joindre à l'email
                pdf_content, _mime = self.env['ir.actions.report']._render_qweb_pdf(
                    'account.report_invoice',
                    res_ids=[invoice.id],
                )
                attachment = self.env['ir.attachment'].create({
                    'name': 'Facture_%s.pdf' % (invoice.name or 'brouillon'),
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_content).decode(),
                    'res_model': 'account.move',
                    'res_id': invoice.id,
                    'mimetype': 'application/pdf',
                })
                template.send_mail(
                    invoice.id,
                    force_send=True,
                    email_values={'attachment_ids': [(4, attachment.id)]},
                )
                email_sent = True

        # Notification résumé
        if email_sent:
            self.env['bus.bus']._sendone(
                self.env.user.partner_id,
                'notification',
                {
                    'type': 'success',
                    'title': _('Facture envoyée'),
                    'message': _('La facture a été confirmée et envoyée à %s') % self.partner_id.email,
                    'sticky': False,
                },
            )

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

    def action_view_picking(self):
        """Ouvre le bon de livraison généré."""
        self.ensure_one()
        if not self.stock_picking_id:
            raise UserError(_("Aucun bon de livraison n'a encore été généré."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bon de livraison'),
            'res_model': 'stock.picking',
            'res_id': self.stock_picking_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

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
