# app/main.py
from fastapi import FastAPI, File, UploadFile, Header, HTTPException
from fastapi.responses import JSONResponse
import os, tempfile, subprocess, json, importlib, re

API_KEY = os.environ.get("API_KEY", "change_me")

app = FastAPI(title="flytax-api")


def try_parse_with_import(pdf_path: str):
    """
    Essaie d'importer flytax et d'appeler une fonction de parsing.
    Si ça échoue, tente la CLI 'python -m flytax <file>'.
    Retourne un dict (idéal) ou { "raw_text": "..." }.
    """
    # 1) try common imports
    try:
        # try top-level
        mod = importlib.import_module("flytax")
        if hasattr(mod, "parse_pdf"):
            return mod.parse_pdf(pdf_path)
        # try submodule common names
    except Exception:
        pass

    for candidate in ("flytax.parser", "flytax.extract", "flytax.core"):
        try:
            mod = importlib.import_module(candidate)
            if hasattr(mod, "parse_pdf"):
                return mod.parse_pdf(pdf_path)
            if hasattr(mod, "extract"):
                return mod.extract(pdf_path)
        except Exception:
            pass

    # 2) fallback to CLI if package exposes a CLI that prints JSON
    try:
        proc = subprocess.run(
            ["python", "-m", "flytax", pdf_path],
            capture_output=True, text=True, timeout=120
        )
        out = proc.stdout.strip()
        if not out and proc.stderr:
            out = proc.stderr.strip()
        try:
            return json.loads(out)
        except Exception:
            return {"raw_text": out}
    except Exception as e:
        raise RuntimeError(f"Parsing fallback failed: {e}")


amount_regex = re.compile(r"(\d{1,3}(?:[ \u00A0\.,]\d{3})*(?:[.,]\d{2})?)")


def str_to_float(s):
    if s is None: return None
    s = s.strip().replace("\u00A0", " ").replace(" ", "")
    s = s.replace(",", ".")
    try:
        return float(re.sub(r"[^\d\.]", "", s))
    except:
        return None


def find_amount_after_label(text: str, label_patterns):
    """
    Cherche dans text un montant après un label (ou plusieurs patterns).
    label_patterns peut être une liste de regex strings.
    Retourne le premier montant trouvé.
    """
    if not text:
        return None
    for pat in label_patterns:
        # chercher "LABEL ... 1 234,56" sur plusieurs lignes possibles
        regex = re.compile(rf"{pat}[^0-9\n\r\-]{{0,50}}({amount_regex.pattern})", re.IGNORECASE)
        m = regex.search(text)
        if m:
            return str_to_float(m.group(1))
    return None


def postprocess(parsed):
    """
    Retourne un dict propre avec les clés recherchées :
    'montant_imposable', 'cumul_imposable', 'frais_emploi', 'decouchers_fpro', 'raw_text'
    """
    result = {
        "montant_imposable": None,
        "cumul_imposable": None,
        "frais_emploi": 0.0,
        "decouchers_fpro": None,
        "raw_text": ""
    }

    # Si parsed est déjà un dict avec clefs claires -> on prend direct
    if isinstance(parsed, dict):
        # raw text
        if "raw_text" in parsed:
            result["raw_text"] = parsed.get("raw_text") or ""
        else:
            # try combine text-ish fields
            result["raw_text"] = parsed.get("text", "") or parsed.get("raw", "") or json.dumps(parsed)

        # direct keys
        for k in ("montant_imposable", "cumul_imposable", "frais_emploi", "decouchers_fpro"):
            if k in parsed and parsed[k] not in (None, ""):
                try:
                    result[k] = float(str(parsed[k]).replace(" ", "").replace(",", "."))
                except:
                    pass

        # try lines dict if exists
        lines = parsed.get("lines") or parsed.get("items") or parsed.get("rows") or None
        if isinstance(lines, dict):
            # labels to sum for frais_emploi
            frais_labels = ["IND.REPAS","INDEMNITE REPAS","IR.FIN ANNEE DOUBL","IND. TRANSPORT",
                            "IND. TRANSPORT EXO","FRAIS REELS TRANSP","R. FRAIS DE TRANSPORT",
                            "IR EXONEREES","IR NON EXONEREES"]
            for key, val in lines.items():
                if not val: continue
                for lab in frais_labels:
                    if lab.lower() in key.lower():
                        fv = str_to_float(val)
                        if fv: result["frais_emploi"] += fv
                if "DECOUCH" in key.upper() and "F.PRO" in key.upper():
                    df = str_to_float(val)
                    if df: result["decouchers_fpro"] = df

    else:
        # parsed is not dict -> fallback to raw text
        result["raw_text"] = str(parsed)

    # If we still miss some fields, try to regex search the raw_text
    raw = result["raw_text"] or ""
    if not result["montant_imposable"]:
        m = find_amount_after_label(raw, [r"Montant\s+imposable", r"Montant\s+imposable\s*[:\-]"])
        if m: result["montant_imposable"] = m
    if not result["cumul_imposable"]:
        m = find_amount_after_label(raw, [r"Cumul\s+imposable", r"Cumul\s+imposable\s*[:\-]"])
        if m: result["cumul_imposable"] = m

    # frais_emploi: try summing labels if still zero
    if (not result["frais_emploi"] or result["frais_emploi"] == 0.0):
        frais_labels = ["IND.REPAS","INDEMNITE REPAS","IR.FIN ANNEE DOUBL","IND. TRANSPORT",
                        "IND. TRANSPORT EXO","FRAIS REELS TRANSP","R. FRAIS DE TRANSPORT",
                        "IR EXONEREES","IR NON EXONEREES"]
        s = 0.0
        found_any = False
        for lab in frais_labels:
            v = find_amount_after_label(raw, [lab, lab.replace(".", r"\.")])
            if v:
                s += v
                found_any = True
        if found_any:
            result["frais_emploi"] = round(s, 2)

    if not result["decouchers_fpro"]:
        d = find_amount_after_label(raw, [r"I\.?DECOUCHERS\s*F\.?PRO", r"DECOUCHERS F\.?PRO", r"Découchers F PRO"])
        if d:
            result["decouchers_fpro"] = d

    # final cleanup: convert to numbers where possible
    for k in ("montant_imposable", "cumul_imposable", "decouchers_fpro"):
        if isinstance(result[k], str):
            result[k] = str_to_float(result[k])

    result["frais_emploi"] = float(result.get("frais_emploi") or 0.0)
    return result


@app.post("/extract")
async def extract_payroll(file: UploadFile = File(...), x_api_key: str = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized - bad API key")

    suffix = os.path.splitext(file.filename)[1] or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        parsed = try_parse_with_import(tmp_path)
    except Exception as e:
        # return verbose error for debug (you can tone it down in prod)
        return JSONResponse(status_code=500, content={"error": str(e)})

    finally:
        try:
            os.remove(tmp_path)
        except:
            pass

    processed = postprocess(parsed)
    # return both raw parsed output + processed summary
    return {"parsed_raw": parsed, "summary": processed}
