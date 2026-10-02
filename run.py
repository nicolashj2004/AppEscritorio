"""Inicia la app de finanzas y la abre en el navegador.

Uso:  python run.py [--port 5050] [--no-browser]
También es el punto de entrada del ejecutable MisFinanzas.exe.
"""
import argparse
import logging
import sys
import threading
import urllib.request
import webbrowser

import flask.cli

from finanzas.app import create_app


def already_running(url):
    """True si ya hay una copia de la app respondiendo en esa dirección."""
    try:
        with urllib.request.urlopen(f"{url}/api/info", timeout=1) as res:
            return res.status == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Mis Finanzas - app local")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--no-browser", action="store_true",
                        help="No abrir el navegador automáticamente")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    if already_running(url):
        # Doble clic con la app ya abierta: solo se abre otra pestaña
        print(f"Mis Finanzas ya está abierta en {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    app = create_app()
    print("\n  Mis Finanzas")
    print(f"  Abierta en: {url}")
    print(f"  Tus datos:  {app.config['DB_PATH']}")
    print("\n  Deja esta ventana abierta mientras usas la app.")
    print("  Para cerrar la app, cierra esta ventana.\n")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Solo escucha en localhost: los datos nunca salen de tu equipo
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # en el .exe la ventana se cerraría sin mostrar el error
        print(f"\n  Error al iniciar Mis Finanzas: {exc}")
        if getattr(sys, "frozen", False):
            input("  Presiona Enter para cerrar...")
        raise SystemExit(1)
