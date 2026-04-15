# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class FacturationScanController(http.Controller):
    """
    Routes JSON pour l'interface de scan.
    Utilisées par le widget OWL barcode_scanner.
    """

    @http.route(
        '/facturation_scan/get_product_by_barcode',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def get_product_by_barcode(self, barcode):
        """
        Recherche un produit par son code-barres.
        Retourne : {id, name, barcode, price} ou {error}.
        """
        if not barcode or not barcode.strip():
            return {'error': 'Code-barres manquant'}

        product = request.env['product.product'].search(
            [('barcode', '=', barcode.strip())], limit=1
        )

        if not product:
            return {'error': f"Produit introuvable : {barcode}"}

        return {
            'id': product.id,
            'name': product.display_name,
            'barcode': product.barcode,
            'price': product.lst_price,
        }

    @http.route(
        '/facturation_scan/add_to_session',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def add_to_session(self, session_id, barcode):
        """
        Ajoute un produit (via barcode) à une session existante.
        Délègue à scan.session.action_add_by_barcode.
        """
        session = request.env['scan.session'].browse(int(session_id))
        if not session.exists():
            return {'error': 'Session introuvable'}
        return session.action_add_by_barcode(barcode)
