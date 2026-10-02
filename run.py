"""Inicia la app de finanzas y la abre en el navegador.

Uso:  python run.py [--port 5050] [--no-browser] [--tray]
También es el punto de entrada del ejecutable MisFinanzas.exe, que se compila sin
ventana de consola: la app se controla desde un ícono junto al reloj (bandeja del
sistema) o con el botón "Cerrar la app" de la propia interfaz.
"""
import argparse
import logging
import os
import sys
import threading
import urllib.request
import webbrowser

from werkzeug.serving import make_server

from finanzas import db
from finanzas.app import create_app

APP_NAME = "Mis Finanzas"


def already_running(url):
    """True si ya hay una copia de la app respondiendo en esa dirección."""
    try:
        with urllib.request.urlopen(f"{url}/api/info", timeout=1) as res:
            return res.status == 200
    except Exception:
        return False


def show_error(message):
    """Muestra un error: en consola, o en una ventana si no hay consola (.exe)."""
    print(f"\n  Error: {message}")
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)


def redirect_output_if_windowless():
    """Sin consola (exe con --windowed) stdout/stderr son None: se envían a un log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = os.path.dirname(os.path.abspath(db.DEFAULT_DB_PATH))
    os.makedirs(log_dir, exist_ok=True)
    log = open(os.path.join(log_dir, "misfinanzas.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def tray_icon_image():
    from PIL import Image

    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return Image.open(os.path.join(here, "finanzas", "static", "icon.ico"))


def run_tray(url, stop):
    """Ícono en la bandeja del sistema. Bloquea hasta que se elige "Salir".

    Devuelve False si no se pudo mostrar (p. ej. sin escritorio gráfico).
    """
    try:
        import pystray  # en Linux sin escritorio gráfico falla al importar
    except Exception:
        return False

    def open_app(_icon=None, _item=None):
        webbrowser.open(url)

    icon = pystray.Icon(
        "MisFinanzas", tray_icon_image(), APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem("Abrir Mis Finanzas", open_app, default=True),
            pystray.MenuItem("Salir", lambda _icon, _item: stop()),
        ),
    )
    stop.icon = icon

    def on_ready(ic):
        ic.visible = True
        try:
            ic.notify("La app está abierta en tu navegador. Para cerrarla usa este ícono "
                      "junto al reloj o el botón 'Cerrar la app'.", APP_NAME)
        except Exception:
            pass

    try:
        icon.run(setup=on_ready)
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning("No se pudo mostrar el ícono: %s", exc)
        stop.icon = None
        return False


def main():
    parser = argparse.ArgumentParser(description="Mis Finanzas - app local")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--no-browser", action="store_true",
                        help="No abrir el navegador automáticamente")
    parser.add_argument("--tray", action="store_true",
                        help="Mostrar un ícono junto al reloj en lugar de usar la consola "
                             "(siempre activo en el .exe)")
    args = parser.parse_args()
    tray = args.tray or getattr(sys, "frozen", False)

    url = f"http://127.0.0.1:{args.port}"
    if already_running(url):
        # Doble clic con la app ya abierta: solo se abre otra pestaña
        print(f"{APP_NAME} ya está abierta en {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    app = create_app()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    # Solo escucha en localhost: los datos nunca salen de tu equipo
    try:
        server = make_server("127.0.0.1", args.port, app, threaded=True)
    except OSError:
        show_error(f"No se pudo iniciar: el puerto {args.port} está ocupado por otro programa.")
        raise SystemExit(1)

    def stop():
        server.shutdown()
        if getattr(stop, "icon", None) is not None:
            stop.icon.stop()

    stop.icon = None
    app.config["SHUTDOWN"] = stop

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"\n  {APP_NAME}")
    print(f"  Abierta en: {url}")
    print(f"  Tus datos:  {app.config['DB_PATH']}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    if not (tray and run_tray(url, stop)):
        print("\n  Deja esta ventana abierta mientras usas la app.")
        print("  Para cerrarla: botón 'Cerrar la app' o cierra esta ventana (Ctrl+C).\n")
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    redirect_output_if_windowless()
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        show_error(f"Error al iniciar {APP_NAME}: {exc}")
        if getattr(sys, "frozen", False) and sys.stdin is not None:
            input("  Presiona Enter para cerrar...")
        raise SystemExit(1)
