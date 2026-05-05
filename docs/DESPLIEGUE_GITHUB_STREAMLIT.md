# Despliegue en GitHub + Streamlit Cloud

## Objetivo

Mantener separado el código de los datos. GitHub debe guardar la app, no la base viva ni las evidencias capturadas en operación.

## Qué sí subir a GitHub

- `app.py`
- `pages/`
- `pages_legacy/`
- `modules/`
- `requirements.txt`
- `README.md`
- `.gitignore`

## Qué NO subir a GitHub cuando ya hay datos reales

- `data/fuel_control.db`
- `data/*.db`
- `data/evidencias/`
- `data/tickets/`
- respaldos manuales de base

Estos archivos quedan excluidos en `.gitignore`.

## Flujo recomendado para actualizar código

```bash
cd ruta/del/proyecto
git status
git pull
# aplicar cambios de código
streamlit run app.py
git add app.py modules pages pages_legacy requirements.txt README.md .gitignore docs
git commit -m "Actualiza control logístico"
git push
```

Streamlit Cloud redeployará automáticamente desde GitHub.

## Mientras sigamos con SQLite en Streamlit Cloud

SQLite sirve para desarrollo, pero no debe considerarse persistencia definitiva en Streamlit Cloud. Antes de cada cambio fuerte:

1. Entrar como administrador.
2. Ir a **Auditoría y correcciones**.
3. Descargar respaldo de la base SQLite.
4. Guardar el respaldo con fecha.
5. Hacer `git push` con cambios de código.
6. Revisar que la app abre y que las rutas/cargas siguen visibles.

## Para operación real

Migrar la base a PostgreSQL externo: Neon, Supabase, Railway, Render PostgreSQL, etc. En ese escenario:

- GitHub sigue guardando código.
- PostgreSQL guarda datos reales.
- Streamlit Cloud usa `secrets.toml` para credenciales.
- Los redeploys no pisan la base.

## Rutas de prueba

En desarrollo, las rutas de prueba deben marcarse como `PRUEBA` o `CAPACITACION` y anularse lógicamente desde **Auditoría y correcciones**. La eliminación definitiva solo se usa si realmente quieres borrar una ruta de prueba con todos sus registros asociados.
