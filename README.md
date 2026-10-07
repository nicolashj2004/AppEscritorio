# 💳 Mis Finanzas

App local para manejar tus finanzas personales mes a mes: tarjetas de crédito y su cupo,
categorías con presupuesto mensual, gastos fijos, metas de ahorro y un dashboard con todo
el comportamiento. Los datos se guardan en una base de datos SQLite **en tu equipo**
(`data/finanzas.db`); nada sale a internet.

## Funcionalidades

- **Dashboard mensual**: ingresos, gastos, balance y tasa de ahorro con comparación contra
  el mes anterior; uso del presupuesto; deuda y cupo disponible de las tarjetas; gráfico de
  ingresos vs gastos de los últimos 12 meses (clic en una barra para ir a ese mes); ritmo de
  gasto diario vs el mes anterior y el presupuesto; gasto por categoría; medios de pago;
  gastos más grandes y alertas (presupuesto excedido, cupo alto, fecha de pago cercana,
  fijos sin registrar).
- **Mes al que corresponde cada movimiento**: por defecto es el mes de la fecha, pero puedes
  cambiarlo (p. ej. el salario pagado el 30 de septiembre cuenta para octubre). En *Ajustes*
  puedes indicar que los ingresos recibidos desde cierto día pasen automáticamente al mes siguiente.
- **Navegación entre meses** con las flechas, el selector de mes o el teclado (← / →, T = hoy).
- **Tarjetas de crédito y débito**: en las de crédito, cupo total, deuda, disponible y % de uso
  calculados al cierre de cada mes; días de corte y de pago (el pago puede caer el mes siguiente
  al corte); compras en cuotas; registro de pagos (los pagos a la
  tarjeta reducen la deuda y **no** cuentan en el total de gastos, para no contar doble; si les
  asignas una categoría, suman al gasto y presupuesto de esa categoría).
  Las tarjetas débito tienen un **saldo**: se recargan (p. ej. la parte del salario que envías
  a Bancolombia para transporte y almuerzos) y cada gasto pagado con ellas lo descuenta. Las
  recargas no cuentan como gasto ni como ingreso.
- **Compras a cuotas**: cada compra tiene su número de cuotas y su propia tasa de interés
  mensual. Cada mes cuenta solo la cuota que se paga (capital + intereses, con los intereses en
  la categoría *Intereses*); la tarjeta muestra el plan de cada compra (cuota actual, saldo y
  mes final), el pago estimado del corte y permite abonar a capital. El dashboard muestra las
  cuotas comprometidas de los próximos 12 meses.
- **Selector Todo / Gastos / Ingresos** fijo en la barra superior para Movimientos, Categorías
  y Gastos fijos.
- **Movimientos**: gastos, ingresos y pagos a tarjeta con categoría, medio de pago, notas;
  filtros, búsqueda y exportación a CSV (se abre en Excel).
- **Categorías y presupuesto**: crea tus propias categorías; presupuesto por defecto o
  específico para un mes; botón para copiar el presupuesto del mes anterior.
- **Gastos e ingresos fijos**: arriendo, servicios, salario… se registran uno por uno (con la
  opción de ajustar el monto ese mes) o todos a la vez.
- **Inversiones**: registra cada inversión con su aplicación (Trii, Tyba, Nu, Binance…) y tipo
  de activo (acciones, ETF, CDT, cripto, pensión voluntaria…); distribución del portafolio por
  tipo y por aplicación, ganancia/pérdida, evolución mes a mes e historial de aportes, retiros
  y actualizaciones de valor. Cada inversión puede estar en **pesos o dólares**; el portafolio
  se ve en COP o USD usando la TRM del mes (escrita a mano o traída de la TRM oficial).
- **Metas de ahorro**: objetivo, abonos/retiros y cuánto ahorrar al mes para cumplirla.
- **Respaldo**: descarga una copia de la base de datos y restáurala desde *Ajustes*.
- Tema claro/oscuro y diseño adaptable a pantallas pequeñas.

## Descargar para Windows (sin instalar nada)

Descarga **[MisFinanzas.exe](https://github.com/nicolashj2004/AppEscritorio/releases/latest/download/MisFinanzas.exe)**
y ábrelo con doble clic. No necesitas Python ni nada más.

- La app se abre en el navegador, sin ventana de consola. Mientras está abierta aparece un
  ícono de Mis Finanzas junto al reloj de Windows (puede estar dentro de la flechita ^):
  clic para volver a abrirla y clic derecho → **Salir** para cerrarla. También puedes usar el
  botón **⏻ Cerrar la app** del menú lateral.
- La primera vez Windows puede mostrar *"Windows protegió su PC"*, porque el programa no está
  firmado: haz clic en **Más información → Ejecutar de todas formas**.
- Los datos se guardan en `%LOCALAPPDATA%\MisFinanzas\finanzas.db` del propio computador.
  Para pasar datos de otro equipo: *Ajustes → Descargar respaldo* en el viejo y
  *Ajustes → Restaurar respaldo* en el nuevo.
- Cada vez que se hace merge a `main`, GitHub compila una versión nueva del `.exe`
  (ver la página *Releases* del repositorio).

## Ejecutar desde el código

## Requisitos

- Python 3.9 o superior ([python.org](https://www.python.org/downloads/)).

## Cómo iniciarla

**Windows**: doble clic en `iniciar.bat`.
**macOS / Linux**: `./iniciar.sh`

La primera vez crea un entorno virtual e instala Flask. Luego abre el navegador en
<http://127.0.0.1:5050>. Para cerrarla, cierra la ventana de la consola (o Ctrl+C).

Manualmente:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip ...
.venv/bin/python run.py                        # opciones: --port 5050 --no-browser
```

### Datos de ejemplo

Para probar con datos ficticios (solo si la base está vacía):

```bash
python seed_demo.py
```

Para empezar de cero, cierra la app y borra `data/finanzas.db`.

## Estructura

```
run.py                  # arranca el servidor y abre el navegador
finanzas/db.py          # esquema SQLite y categorías por defecto
finanzas/app.py         # API REST (Flask) y cálculos del dashboard
finanzas/investments.py # cálculos del portafolio de inversiones
finanzas/installments.py # plan de pagos de las compras a cuotas
finanzas/static/        # interfaz (HTML + CSS + JS, Chart.js incluido: funciona sin internet)
seed_demo.py            # datos de ejemplo
.github/workflows/      # compila MisFinanzas.exe en Windows y lo publica en Releases
tests/                  # pruebas: python -m unittest discover -s tests
```
