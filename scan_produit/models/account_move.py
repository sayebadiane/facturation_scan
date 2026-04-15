# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ─── Champs personnalisés ────────────────────────────────────────────────

    x_reference_client = fields.Char(
        string='Référence client',
        help='Numéro de commande ou référence interne du client',
        copy=False,
    )

    x_commercial_id = fields.Many2one(
        comodel_name='res.users',
        string='Commercial',
        domain=[('share', '=', False)],
        help='Commercial responsable de cette facture',
    )

    x_date_echeance_relance = fields.Date(
        string='Date de relance',
        help='Date à partir de laquelle une relance de paiement doit être effectuée',
        copy=False,
    )

    x_motif_avoir = fields.Selection(
        selection=[
            ('retour', 'Retour marchandise'),
            ('erreur_prix', 'Erreur de prix'),
            ('remise', 'Remise commerciale'),
            ('annulation', 'Annulation de commande'),
            ('autre', 'Autre'),
        ],
        string='Motif avoir',
        help='Motif de l\'avoir (uniquement pour les notes de crédit)',
    )

    x_note_interne = fields.Text(
        string='Note interne',
        help='Note visible uniquement en interne, non imprimée sur la facture',
        copy=False,
    )

    x_statut_relance = fields.Selection(
        selection=[
            ('non_relance', 'Non relancé'),
            ('relance_1', '1ère relance'),
            ('relance_2', '2ème relance'),
            ('relance_3', '3ème relance'),
            ('contentieux', 'Contentieux'),
        ],
        string='Statut relance',
        default='non_relance',
        copy=False,
        tracking=True,
    )

    x_montant_regle = fields.Monetary(
        string='Montant réglé',
        compute='_compute_montant_regle',
        store=True,
        currency_field='currency_id',
    )

    x_montant_restant = fields.Monetary(
        string='Restant dû',
        compute='_compute_montant_regle',
        store=True,
        currency_field='currency_id',
    )

    x_delai_paiement = fields.Integer(
        string='Délai de paiement (jours)',
        compute='_compute_delai_paiement',
        store=False,
        help='Nombre de jours entre la date de facture et la date d\'échéance',
    )

    # ─── Champs calculés ────────────────────────────────────────────────────

    @api.depends('amount_total', 'amount_residual')
    def _compute_montant_regle(self):
        for move in self:
            move.x_montant_regle = move.amount_total - move.amount_residual
            move.x_montant_restant = move.amount_residual

    @api.depends('invoice_date', 'invoice_date_due')
    def _compute_delai_paiement(self):
        for move in self:
            if move.invoice_date and move.invoice_date_due:
                delta = move.invoice_date_due - move.invoice_date
                move.x_delai_paiement = delta.days
            else:
                move.x_delai_paiement = 0

    # ─── Contraintes ────────────────────────────────────────────────────────

    @api.constrains('x_motif_avoir', 'move_type')
    def _check_motif_avoir(self):
        for move in self:
            if move.x_motif_avoir and move.move_type not in ('out_refund', 'in_refund'):
                raise UserError(_("Le motif d'avoir n'est applicable qu'aux notes de crédit."))

    # ─── Onchange ───────────────────────────────────────────────────────────

    @api.onchange('partner_id')
    def _onchange_partner_commercial(self):
        """Pré-remplir le commercial depuis la fiche client."""
        if self.partner_id and hasattr(self.partner_id, 'user_id') and self.partner_id.user_id:
            self.x_commercial_id = self.partner_id.user_id

    # ─── Actions ────────────────────────────────────────────────────────────

    def action_marquer_relance(self):
        """Passe au niveau de relance suivant."""
        ordre = ['non_relance', 'relance_1', 'relance_2', 'relance_3', 'contentieux']
        for move in self:
            idx = ordre.index(move.x_statut_relance or 'non_relance')
            if idx < len(ordre) - 1:
                move.x_statut_relance = ordre[idx + 1]

    def action_reset_relance(self):
        """Remet le statut de relance à zéro."""
        self.write({'x_statut_relance': 'non_relance'})
