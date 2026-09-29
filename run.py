"""Inicia la app de finanzas y la abre en el navegador.

Uso:  python run.py [--port 5050] [--no-browser]
"""
import argparse
import threading
import webbrowser

from finanzas.app import create_app


def main():
    parser = argparse.ArgumentParser(description="Mis Finanzas - app local")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--no-browser", action="store_true",
                        help="No abrir el navegador automáticamente")
    args = parser.parse_args()

    app = create_app()
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  Mis Finanzas corriendo en {url}")
    print(f"  Base de datos: {app.config['DB_PATH']}")
    print("  Presiona Ctrl+C para cerrar.\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Solo escucha en localhost: los datos nunca salen de tu equipo
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
