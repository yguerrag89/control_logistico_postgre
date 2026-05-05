# Control Logístico de Unidades - v1.3

Versión enfocada en navegación simplificada y análisis GPS operativo.

## Cambios principales

- Navegación reducida a 6 páginas principales:
  - Panel General
  - Combustible
  - GPS y Actividad
  - Rutas y Entregas
  - Catálogos
  - Auditoría y Correcciones
- Las páginas anteriores se conservan en `pages_legacy/`.
- Nuevo módulo `modules/gps_analytics.py`.
- Análisis de kilómetros recorridos:
  - km diarios por unidad
  - km totales diarios de flota
  - ranking de actividad por unidad
  - días activos por unidad
- Análisis de inactividad anormal:
  - paradas largas no asociadas a entregas
  - exclusión de base probable
  - exclusión de lugares controlados/autorizados
  - clasificación manual de paradas
- Catálogo de lugares controlados:
  - bases
  - clientes
  - gasolineras
  - talleres
  - paqueterías
  - autorizados

## Ejecución

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Nota operativa

La detección de lugares controlados es por texto de dirección en esta versión. Más adelante conviene migrar a coordenadas/geocercas con radio.

## v1.4 - Validaciones ruta-entrega y conciliación inteligente

Cambios principales:

- La conciliación GPS ya no marca una ruta como "Conciliada con GPS" si alguna entrega queda sin parada GPS asociada.
- Estados de ruta generados tras conciliación:
  - Conciliada completa
  - Conciliación con cercanas
  - Conciliación parcial
  - Conciliación con conflictos
  - Cerrada con inconsistencias
  - Cerrada sin entregas
- La página de entregas valida que la hora de llegada no esté fuera del intervalo de la ruta.
- La página de conciliación muestra advertencias cuando una entrega tiene hora anterior a la salida o posterior al regreso.
- La conciliación ahora restringe paradas GPS al intervalo real de la ruta cuando existen hora de salida y regreso.
- Se agregaron sugerencias de paradas GPS cercanas para entregas sin match exacto.
- Desde Conciliación GPS se puede:
  - corregir la hora de llegada al inicio de la parada sugerida y volver a conciliar;
  - asociar manualmente una entrega a una parada GPS sin cambiar la hora capturada.
- Al cambiar la hora de llegada de una entrega, se invalida el match anterior y se recalcula el estado de la ruta.
- Las evidencias nuevas se guardan con ruta relativa cuando es posible, para mejorar portabilidad.

Caso que corrige esta versión:

- Ruta #1 tenía salida corregida a 11:44, pero la entrega seguía con llegada 11:05.
- La conciliación usa `ruta_entregas.hora_llegada_reportada`, no `rutas.hora_salida_reportada`.
- Ahora la app advierte la inconsistencia y sugiere la parada GPS de Naucalpan 11:46-12:25 como candidata.

## v1.5 — Trazabilidad operativa y panel de pendientes

Cambios incluidos:

- Panel General con cola de pendientes operativos críticos.
- Recalculo global de estados de rutas desde Auditoría y Correcciones.
- Detección de entregas con hora de llegada fuera del intervalo de ruta.
- Detección de rutas con estado de conciliación incoherente.
- Campo `tipo_carga_combustible` para distinguir tanque lleno, parcial, emergencia, garrafón, aceite, aditivo u otro.
- Recalculo de `calidad_registro` en cargas de combustible.
- Tabla `archivos_adjuntos` para trazabilidad documental de tickets/evidencias.
- Evidencias y tickets nuevos guardan rutas relativas cuando es posible.
- Normalizador de rutas antiguas de evidencias.
- Clasificación de paradas GPS con una sola clasificación activa por parada.
- Creación de lugares controlados directamente desde paradas GPS frecuentes.

Recomendación operativa: antes de evaluar rendimiento o inactividad como dato definitivo, usar Auditoría y Correcciones para recalcular estados/calidad y empezar a clasificar lugares frecuentes.


## v1.6 — Operación real por roles y cierre de ruta

Esta versión agrega:

- Selector lateral de operador/rol para trazabilidad práctica.
- Modo chofer: captura rápida de ruta, entrega, estatus y foto.
- Cierre operativo de ruta con revisión de entregas, GPS, evidencias e incidencias.
- Catálogo avanzado de destinos/lugares: cliente comercial, contacto, horario, cita, tiempo promedio y exclusión de alertas.
- Vinculación de entregas a destinos validados (`destino_id`) para dejar de depender solo de texto libre.
- Costos adicionales de operación: casetas, maniobras, estacionamiento, viáticos, mantenimiento, refacciones, multas, lavado u otros.
- Panel general con costo logístico/km usando combustible + costos adicionales.

Flujo recomendado:

1. Registrar cargas de combustible con ticket/folio.
2. Capturar rutas y entregas desde el modo chofer.
3. Importar GPS.
4. Conciliar GPS contra entregas.
5. Ejecutar cierre operativo.
6. Convertir paradas frecuentes en destinos/lugares controlados.
7. Revisar el panel general para atacar pendientes.

## v1.7 desarrollo: roles reales Administrador / Chofer

Esta versión elimina el selector libre de roles. Ahora hay acceso con usuario y contraseña:

- Administrador: `admin` / `admin.2026`
- Choferes activos: usuario = nombre del chofer; contraseña = `nombre del chofer.2026`

Ejemplo: si el conductor se llama `José Luis`, su contraseña de desarrollo será `José Luis.2026`.

### Restricciones del rol Chofer

El chofer solo puede entrar a **Rutas y entregas > Modo chofer**. Desde ahí puede:

- crear su ruta rápida del día;
- registrar entregas/visitas;
- capturar hora de llegada;
- capturar estatus/motivo/observaciones;
- subir evidencia;
- cerrar la ruta.

No puede acceder a Panel general, Combustible, GPS, Catálogos ni Auditoría.

### Nota importante para Streamlit Cloud y SQLite

Para esta fase de desarrollo se conserva SQLite en `data/fuel_control.db`. La app guarda las capturas de chofer en esa base mientras está corriendo. Sin embargo, en Streamlit Cloud los cambios realizados a archivos locales, incluyendo SQLite, no deben considerarse almacenamiento persistente definitivo después de reinicios o redeploys. Por eso se agregó un botón en **Auditoría y correcciones** para descargar respaldos de la base.

Cuando la app pase a uso operativo real, conviene migrar a una base externa como PostgreSQL/Neon/Supabase para persistencia confiable.


## v1.8 - Chofer con captura de combustible

Cambios de esta versión de desarrollo:

- Solo existen dos roles: `Administrador` y `Chofer`.
- El administrador puede acceder a toda la aplicación.
- El chofer puede acceder únicamente a:
  - `Rutas y entregas` en modo chofer.
  - `Combustible` en modo captura móvil.
- El chofer no puede acceder a historial completo, rendimiento GPS, costos, GPS, catálogos ni auditoría.
- La carga de combustible registrada por chofer se guarda en `cargas_combustible` con:
  - `conductor_id` vinculado al usuario autenticado.
  - `origen_registro = chofer_movil`.
  - `estado_validacion = PENDIENTE_VALIDACION`.
  - ticket/foto como adjunto trazable cuando se sube imagen.
- Para desarrollo, cada chofer activo tiene usuario igual a su nombre y contraseña `nombre.2026`.

> Nota de despliegue: SQLite sirve para esta fase de desarrollo, incluso subiendo una base inicial al repositorio, pero en Streamlit Cloud los cambios locales del archivo `.db` no deben considerarse persistencia definitiva a largo plazo. Para operación real conviene migrar a PostgreSQL/Neon/Supabase.

## v1.10 — Rutas de prueba y protección GitHub/Streamlit

Cambios incluidos:

- Campo `tipo_ruta` en rutas: `OPERATIVA`, `PRUEBA`, `CAPACITACION`.
- El modo chofer crea rutas como `OPERATIVA`.
- El administrador puede marcar rutas como prueba/capacitación.
- El administrador puede anular rutas de prueba sin borrarlas físicamente.
- La anulación lógica deja `activo=0`, `estado_ruta=ANULADA_PRUEBA`, motivo, usuario y fecha de anulación.
- La eliminación definitiva queda disponible solo en Auditoría y correcciones como herramienta de desarrollo. Borra ruta, entregas, evidencias, matches GPS y gastos relacionados.
- Indicadores y pendientes operativos excluyen rutas no operativas/anuladas.
- Se agregó `.gitignore` para evitar subir la base viva y evidencias/tickets a GitHub.
- Se agregó `docs/DESPLIEGUE_GITHUB_STREAMLIT.md` con el flujo recomendado para actualizar código sin pisar datos.

Recomendación: usa eliminación definitiva solo para rutas creadas por pruebas o capacitación. Para operación real, usa anulación lógica.


## PostgreSQL / Neon para Streamlit Cloud

Esta versión puede trabajar con SQLite local o PostgreSQL en Neon. Para Streamlit Cloud usa Neon:

1. Crea el proyecto/base en Neon.
2. En Streamlit Cloud agrega Secrets:

```toml
DB_BACKEND = "postgres"
DATABASE_URL = "postgresql://USUARIO:CONTRASENA@HOST/neondb?sslmode=require&channel_binding=require"
```

3. Migra la base SQLite local a Neon:

```bash
python scripts/migrate_sqlite_to_postgres.py --sqlite data/fuel_control.db
```

4. Sube el código a GitHub. No subas `.streamlit/secrets.toml`, `data/*.db`, tickets ni evidencias.

Más detalle: `docs/NEON_POSTGRES_STREAMLIT.md`.

## v1.12 - Optimización de rendimiento

Cambios principales:

- Reutilización de conexiones PostgreSQL/Neon mediante pool interno.
- Índices automáticos para tablas GPS, rutas, combustible, entregas y archivos.
- Caché de lecturas operativas con TTL corto para Panel General, GPS y análisis.
- Agregados diarios GPS calculados en SQL en lugar de traer todos los movimientos a pandas.
- Panel General más liviano: algunos análisis se cargan solo al abrir su pestaña.
- Movimientos GPS crudos bajo demanda para evitar cargar tablas pesadas por defecto.

Notas:

- Si se registra o corrige un dato, algunas métricas cacheadas pueden tardar hasta 60 segundos en refrescarse.
- Los índices se crean automáticamente al arrancar la app con `init_db()`.
- En Neon puedes ajustar el pool con el secret/env `DB_POOL_MAX`, por ejemplo `DB_POOL_MAX = "5"`.
