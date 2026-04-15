/** @odoo-module **/

/**
 * Widget OWL : BarcodeScannerField
 * ─────────────────────────────────
 * Champ personnalisé permettant le scan de code-barres dans
 * le formulaire d'une session (scan.session).
 *
 * Comportement :
 *   1. L'utilisateur scanne (lecteur USB) ou saisit un code-barres
 *   2. En appuyant sur Entrée (ou le bouton "Ajouter"), le widget
 *      appelle scan.session.action_add_by_barcode via ORM
 *   3. Le formulaire est rechargé pour afficher la nouvelle ligne
 *
 * Compatible Odoo 18 Community (OWL 2 + @web services)
 */

import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class BarcodeScannerField extends Component {
    static template = "facturation_scan.BarcodeScannerField";
    static props = {
        ...standardFieldProps,
    };

    // ── Setup ──────────────────────────────────────────────────────────────

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        // État local du widget (indépendant du champ Odoo)
        this.state = useState({
            value: "",       // valeur saisie dans l'input
            scanning: false, // true pendant l'appel RPC
        });

        this.barcodeInput = useRef("barcodeInput");

        // Mise au focus automatique au chargement de la vue
        onMounted(() => {
            this._focusInput();
        });
    }

    // ── Helpers ────────────────────────────────────────────────────────────

    /**
     * Met le focus sur le champ de saisie.
     */
    _focusInput() {
        if (this.barcodeInput.el) {
            this.barcodeInput.el.focus();
        }
    }

    /**
     * Retourne l'ID de la session courante (ou null si nouvelle fiche).
     */
    get _sessionId() {
        return this.props.record.data.id || null;
    }

    // ── Traitement du scan ─────────────────────────────────────────────────

    /**
     * Point d'entrée principal : traite un code-barres.
     * @param {string} barcode
     */
    async _processBarcode(barcode) {
        barcode = (barcode || "").trim();
        if (!barcode) return;

        // La session doit être sauvegardée pour avoir un ID DB
        let sessionId = this._sessionId;
        if (!sessionId) {
            // Tentative de sauvegarde automatique
            try {
                await this.props.record.save();
                sessionId = this.props.record.data.id;
            } catch {
                /* sauvegarde impossible (champs obligatoires manquants) */
            }

            if (!sessionId) {
                this.notification.add(
                    "Sélectionnez un client et enregistrez la session avant de scanner.",
                    { type: "warning", title: "Session non sauvegardée" }
                );
                this.state.value = "";
                this._focusInput();
                return;
            }
        }

        this.state.scanning = true;

        try {
            const result = await this.orm.call(
                "scan.session",
                "action_add_by_barcode",
                [[sessionId], barcode]
            );

            if (result.error) {
                this.notification.add(result.error, {
                    type: "danger",
                    title: "Produit refusé",
                });
            } else {
                // Notification principale : produit ajouté
                this.notification.add(
                    `${result.product_name} — qté : ${result.quantity}`,
                    { type: "success", title: "Ajouté au panier" }
                );
                // Notification secondaire si le produit a une note scan
                if (result.note) {
                    this.notification.add(result.note, {
                        type: "warning",
                        title: result.product_name,
                    });
                }
                // Rechargement du formulaire pour afficher la nouvelle ligne
                await this.props.record.load();
            }
        } catch (err) {
            this.notification.add(
                "Erreur de communication avec le serveur.",
                { type: "danger" }
            );
        } finally {
            this.state.value = "";
            this.state.scanning = false;
            this._focusInput();
        }
    }

    // ── Gestionnaires d'événements ─────────────────────────────────────────

    /**
     * Appelé à chaque touche dans l'input.
     * Déclenche le traitement sur Entrée.
     */
    async onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            ev.stopPropagation();
            await this._processBarcode(this.state.value);
        }
    }

    /**
     * Appelé au clic sur le bouton "Ajouter".
     */
    async onClickAdd() {
        await this._processBarcode(this.state.value);
    }
}

// ── Enregistrement dans le registre Odoo ──────────────────────────────────

registry.category("fields").add("barcode_scanner", {
    component: BarcodeScannerField,
    supportedTypes: ["char"],
    extractProps({ attrs, options }, dynamicInfo) {
        return {
            readonly: dynamicInfo.readonly,
        };
    },
});
