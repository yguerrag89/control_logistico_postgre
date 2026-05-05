# Despliegue con PostgreSQL en Neon

## 1. Configurar secretos

En local crea `.streamlit/secrets.toml`:

```toml
DB_BACKEND = "postgres"
DATABASE_URL = "postgresql://USUARIO:CONTRASENA@HOST/neondb?sslmode=require&channel_binding=require"
```

En Streamlit Cloud ve a **Settings → Secrets** y pega lo mismo.

Nunca subas `.streamlit/secrets.toml` a GitHub.

## 2. Migrar datos desde SQLite

Desde la raíz del proyecto:

```bash
pip install -r requirements.txt
python scripts/migrate_sqlite_to_postgres.py --sqlite data/fuel_control.db
```

El script crea el esquema en Neon, carga los datos de SQLite y respeta los IDs.

## 3. Probar local contra Neon

```bash
streamlit run app.py
```

Verifica:

- login admin;
- unidades y conductores;
- cargas de combustible;
- GPS;
- crear una ruta de prueba;
- registrar una carga como chofer.

## 4. Subir código a GitHub

```bash
git add .
git commit -m "Soporte PostgreSQL Neon"
git push
```

## 5. Recomendaciones

- No subas `data/*.db` a GitHub.
- No subas `data/evidencias/` ni `data/tickets/`.
- Haz respaldo de SQLite antes de migrar.
- La base viva en Cloud debe ser Neon, no SQLite.
