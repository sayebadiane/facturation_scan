/** @odoo-module **/

/**
 * Widget OWL : BarcodeScannerField
 * ─────────────────────────────────
 * Trois modes de saisie :
 *   1. Lecteur USB/Bluetooth  → service barcode Odoo (écoute globale, focus non requis)
 *   2. Webcam                 → BarcodeDetector API (Chrome/Edge natif, sans librairie)
 *   3. Saisie manuelle        → input + touche Entrée
 *
 * Compatible Odoo 19 Community (OWL 2)
 */

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService, useBus } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

// Formats code-barres courants (produits retail, logistique, QR)
const BARCODE_FORMATS = [
    'code_128', 'ean_13', 'ean_8', 'upc_a', 'upc_e',
    'qr_code', 'code_39', 'itf', 'data_matrix',
];

// Délai anti-double-scan (ms) : ignore tout nouveau scan pendant cette durée
const SCAN_COOLDOWN_MS = 1500;

export class BarcodeScannerField extends Component {
    static template = "facturation_scan.BarcodeScannerField";
    static props = {
        ...standardFieldProps,
    };

    // ── Setup ──────────────────────────────────────────────────────────────

    setup() {
        this.orm          = useService("orm");
        this.notification = useService("notification");
        const barcodeService = useService("barcode");

        this.state = useState({
            value:         "",     // saisie manuelle
            scanning:      false,  // RPC en cours
            cameraActive:  false,  // overlay caméra visible
            cameraLoading: false,  // caméra en cours de démarrage
            lastDetected:  "",     // nom du dernier produit détecté (badge)
        });

        this.barcodeInput = useRef("barcodeInput");
        this.videoRef     = useRef("videoRef");

        // Attributs caméra (non réactifs, gestion manuelle)
        this._cameraStream    = null;
        this._barcodeDetector = null;
        this._detecting       = false;
        this._lastScanAt      = 0;

        onMounted(() => this._focusInput());

        onWillUnmount(() => this._stopCamera());

        // ── Service barcode Odoo (lecteur HID) ────────────────────────────
        // Écoute globale : s'active quand le lecteur scanne sans que notre
        // input ait le focus (évite le double-traitement via onKeydown).
        useBus(barcodeService.bus, "barcode_scanned", async (ev) => {
            const inputEl = this.barcodeInput.el;
            if (inputEl && document.activeElement === inputEl) return; // onKeydown gère
            const barcode = ev.detail?.barcode ?? ev.detail;
            if (typeof barcode === "string" && barcode.trim()) {
                await this._processBarcode(barcode.trim());
            }
        });
    }

    // ── Helpers ────────────────────────────────────────────────────────────

    _focusInput() {
        this.barcodeInput.el?.focus();
    }

    get _sessionId() {
        return this.props.record.resId || null;
    }

    // ── Traitement du scan ─────────────────────────────────────────────────

    async _processBarcode(barcode) {
        barcode = (barcode || "").trim();
        if (!barcode) return;

        let sessionId = this._sessionId;
        if (!sessionId) {
            try {
                await this.props.record.save();
                sessionId = this.props.record.resId;
            } catch { /* champs obligatoires manquants */ }

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
                this.notification.add(
                    `${result.product_name} — qté : ${result.quantity}`,
                    { type: "success", title: "Ajouté au panier" }
                );
                if (result.note) {
                    this.notification.add(result.note, {
                        type: "warning",
                        title: result.product_name,
                    });
                }
                // Badge détection caméra
                if (this.state.cameraActive) {
                    this.state.lastDetected = result.product_name;
                    setTimeout(() => { this.state.lastDetected = ""; }, SCAN_COOLDOWN_MS);
                }
                await this.props.record.load();
            }
        } catch {
            this.notification.add("Erreur de communication avec le serveur.", { type: "danger" });
        } finally {
            this.state.value    = "";
            this.state.scanning = false;
            if (!this.state.cameraActive) this._focusInput();
        }
    }

    // ── Caméra — ouverture ─────────────────────────────────────────────────

    async openCamera() {
        // Vérification support navigateur
        if (!("BarcodeDetector" in window)) {
            this.notification.add(
                "La détection par caméra nécessite Chrome 83+ ou Edge 83+. "
                + "Firefox ne supporte pas encore cette API.",
                { type: "warning", title: "Navigateur non compatible" }
            );
            return;
        }

        this.state.cameraLoading = true;

        try {
            // Demande d'accès à la caméra
            this._cameraStream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: "environment",   // caméra arrière sur mobile
                    width:  { ideal: 1280 },
                    height: { ideal: 720 },
                },
            });

            this.state.cameraActive  = true;
            this.state.cameraLoading = false;

            // Attendre le prochain cycle de rendu pour que le <video> soit dans le DOM
            await new Promise(r => setTimeout(r, 50));

            const video = this.videoRef.el;
            if (!video) { this._stopCamera(); return; }
            video.srcObject = this._cameraStream;
            await video.play();

            // Initialisation BarcodeDetector
            let formats = BARCODE_FORMATS;
            try {
                const supported = await BarcodeDetector.getSupportedFormats();
                const filtered  = supported.filter(f => BARCODE_FORMATS.includes(f));
                if (filtered.length) formats = filtered;
            } catch { /* utiliser la liste par défaut */ }

            this._barcodeDetector = new BarcodeDetector({ formats });
            this._detecting       = true;
            this._lastScanAt      = 0;
            this._runDetectionLoop();

        } catch (err) {
            this.state.cameraActive  = false;
            this.state.cameraLoading = false;
            this._stopCamera();

            const msg = err.name === "NotAllowedError"
                ? "Accès à la caméra refusé. Autorisez-le dans les paramètres du navigateur."
                : `Impossible d'accéder à la caméra : ${err.message}`;
            this.notification.add(msg, { type: "danger", title: "Caméra" });
        }
    }

    // ── Caméra — boucle de détection ───────────────────────────────────────

    _runDetectionLoop() {
        const loop = async () => {
            if (!this._detecting) return;

            const video = this.videoRef.el;
            // Attendre que la vidéo soit prête (readyState >= HAVE_CURRENT_DATA)
            if (!video || video.readyState < 2) {
                requestAnimationFrame(loop);
                return;
            }

            // Cooldown anti-double-scan
            if (Date.now() - this._lastScanAt < SCAN_COOLDOWN_MS) {
                requestAnimationFrame(loop);
                return;
            }

            try {
                const results = await this._barcodeDetector.detect(video);
                if (results.length > 0) {
                    this._lastScanAt = Date.now();
                    await this._processBarcode(results[0].rawValue);
                }
            } catch { /* frame en cours de décodage, ignorer */ }

            if (this._detecting) requestAnimationFrame(loop);
        };

        requestAnimationFrame(loop);
    }

    // ── Caméra — fermeture ─────────────────────────────────────────────────

    closeCamera() {
        this._stopCamera();
        this._focusInput();
    }

    _stopCamera() {
        this._detecting = false;
        if (this._cameraStream) {
            this._cameraStream.getTracks().forEach(t => t.stop());
            this._cameraStream = null;
        }
        this._barcodeDetector   = null;
        this.state.cameraActive  = false;
        this.state.cameraLoading = false;
        this.state.lastDetected  = "";
    }

    // Clic sur le fond de l'overlay ferme la caméra
    onOverlayClick() {
        this.closeCamera();
    }

    // ── Gestionnaires saisie manuelle ──────────────────────────────────────

    async onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            ev.stopPropagation();
            await this._processBarcode(this.state.value);
        }
    }

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
