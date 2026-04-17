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
        this._zxingControls   = null;
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

    // ── Validation lecture ─────────────────────────────────────────────────

    /**
     * Rejette les lectures parasites : caractères non-ASCII, code trop court.
     * Une mauvaise lecture caméra produit souvent des caractères accentués
     * ou des symboles (ex: "Bà&FATQOKQ" au lieu de "B01FQTAOKA").
     */
    _isValidRead(barcode) {
        if (!barcode || barcode.trim().length < 3) return false;
        // Accepter uniquement les caractères ASCII imprimables (codes 32-126)
        return /^[\x20-\x7E]+$/.test(barcode.trim());
    }

    // ── Caméra — ouverture ─────────────────────────────────────────────────

    async openCamera() {
        const hasNative = "BarcodeDetector" in window;
        const hasZXing  = typeof window.ZXing !== "undefined";

        if (!hasNative && !hasZXing) {
            this.notification.add(
                "Aucune API de scan disponible. Rechargez la page en mode debug=assets.",
                { type: "danger", title: "Scan caméra indisponible" }
            );
            return;
        }

        this.state.cameraLoading = true;

        try {
            this._cameraStream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
            });

            this.state.cameraActive  = true;
            this.state.cameraLoading = false;

            // Laisser OWL rendre le <video> dans le DOM
            await new Promise(r => setTimeout(r, 50));
            const video = this.videoRef.el;
            if (!video) { this._stopCamera(); return; }

            if (hasNative) {
                // ── Chrome / Edge : BarcodeDetector natif ──
                video.srcObject = this._cameraStream;
                await video.play();

                let formats = BARCODE_FORMATS;
                try {
                    const supported = await BarcodeDetector.getSupportedFormats();
                    const filtered  = supported.filter(f => BARCODE_FORMATS.includes(f));
                    if (filtered.length) formats = filtered;
                } catch { /* liste par défaut */ }

                this._barcodeDetector = new BarcodeDetector({ formats });
                this._detecting = true;
                this._lastScanAt = 0;
                this._runDetectionLoop();

            } else {
                // ── Firefox / Safari : fallback ZXing ──
                const hints = new Map([[window.ZXing.DecodeHintType.TRY_HARDER, true]]);
                const reader = new window.ZXing.BrowserMultiFormatReader(hints);
                this._zxingControls = await reader.decodeFromStream(
                    this._cameraStream,
                    video,
                    async (result, _err) => {
                        if (!result) return;
                        if (Date.now() - this._lastScanAt < SCAN_COOLDOWN_MS) return;
                        this._lastScanAt = Date.now();

                        const raw   = result.getText() || "";
                        const clean = raw.trim();

                        if (this._isValidRead(clean)) {
                            // Lecture propre → traitement automatique
                            await this._processBarcode(clean);
                        } else {
                            // Lecture douteuse (caractères parasites) →
                            // on affiche dans le champ pour correction manuelle
                            const sanitized = clean.replace(/[^\x20-\x7E]/g, "");
                            this.state.value = sanitized;
                            this.notification.add(
                                `Lecture incertaine — vérifiez et appuyez sur Entrée`,
                                { type: "warning", title: `Détecté : "${sanitized}"` }
                            );
                        }
                    }
                );
            }

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
                    const value = results[0].rawValue;
                    if (this._isValidRead(value)) {
                        this._lastScanAt = Date.now();
                        await this._processBarcode(value.trim());
                    }
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
        if (this._zxingControls) {
            try { this._zxingControls.stop(); } catch { /* ignore */ }
            this._zxingControls = null;
        }
        if (this._cameraStream) {
            this._cameraStream.getTracks().forEach(t => t.stop());
            this._cameraStream = null;
        }
        this._barcodeDetector    = null;
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
