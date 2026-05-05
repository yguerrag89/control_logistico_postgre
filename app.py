from pathlib import Path
import streamlit as st

from modules.bootstrap import bootstrap_if_needed
from modules.db import ensure_directories, init_db, get_db_backend
from modules.session import require_auth

st.set_page_config(
    page_title="Control Logístico de Unidades",
    page_icon="🚚",
    layout="wide",
)

ensure_directories()
init_db()
bootstrap_if_needed()
ctx = require_auth()

st.title("🚚 Control Logístico de Unidades - Baro Industrial")
st.caption(f"v1.11 desarrollo: PostgreSQL/Neon listo para Streamlit Cloud · Backend activo: {get_db_backend()}")

if ctx["rol"] == "Chofer":
    st.success(f"Bienvenido, {ctx.get('conductor_nombre') or ctx['usuario']}. Tu acceso está limitado a captura operativa de chofer.")
    st.markdown(
        """
### Qué puedes hacer
1. Entra a **Rutas y entregas** para crear/seleccionar tu ruta del día.
2. Registra entregas, estatus, observaciones y evidencia fotográfica.
3. Cierra la ruta cuando regreses.
4. Entra a **Combustible** para registrar cargas con foto de ticket.

Las secciones administrativas, GPS, catálogos, auditoría e historial completo están bloqueadas para choferes.
"""
    )
else:
    st.markdown(
        """
### Navegación optimizada
La aplicación está agrupada por flujo de trabajo:

1. **Panel general**: KPIs, GPS, combustible, alertas y calidad de datos.
2. **Combustible**: registrar carga, historial, rendimiento y tickets.
3. **GPS y actividad**: importar GPS, km diarios, inactividad anormal y paradas frecuentes.
4. **Rutas y entregas**: modo chofer, panel de rutas, cierre operativo y conciliación GPS.
5. **Catálogos**: unidades, conductores, destinos, lugares controlados y usuarios.
6. **Auditoría y correcciones**: historial detallado de cambios y reparaciones controladas.

### Control de roles en esta versión
- **Administrador**: puede acceder a toda la app.
- **Chofer**: puede capturar su propia ruta, entregas, evidencias, cierre y cargas de combustible. No puede acceder a historial completo, GPS, catálogos ni auditoría.

Usuarios de desarrollo:
- Administrador: `admin` / `admin.2026`
- Choferes: usuario = nombre del chofer; contraseña = `nombre del chofer.2026`
"""
    )

st.info("Para probar captura de chofer, cierra sesión e ingresa con el nombre de un conductor activo y su contraseña `nombre.2026`.")
