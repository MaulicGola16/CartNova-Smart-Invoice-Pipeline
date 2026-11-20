import json
from pathlib import Path
from flask import Flask, jsonify

app = Flask(__name__)

DATA_PATH = Path(__file__).resolve().parent / "suppliers.json"
with open(DATA_PATH, "r", encoding="utf-8") as f:
    SUPPLIERS = json.load(f)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/suppliers/<gstin>", methods=["GET"])
def get_supplier(gstin: str):
    key = gstin.strip().upper()
    supplier = SUPPLIERS.get(key)
    if not supplier:
        return jsonify({
            "error": "NOT_FOUND",
            "detail": f"Supplier with GSTIN {key} not found"
        }), 404

    return jsonify({
        "gstin": key,
        "name": supplier["name"],
        "city": supplier["city"],
        "active": supplier["active"]
    }), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)