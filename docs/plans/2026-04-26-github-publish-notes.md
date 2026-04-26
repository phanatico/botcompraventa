# Publicación en GitHub y pruebas en dedicado

## Estado actual

- Esta copia no tiene carpeta `.git`.
- `git` sí está instalado en el entorno.
- Para subir cambios hace falta un repositorio destino en GitHub.

## Opción recomendada

1. Crear un repositorio vacío en GitHub.
2. Pasar la URL del repositorio.
3. Inicializar Git en esta carpeta.
4. Hacer primer commit.
5. Añadir remoto y subir.

## Comandos base

```bash
git init
git branch -M main
git add .
git commit -m "Initial customized Telegram shop rework"
git remote add origin <URL_DEL_REPO>
git push -u origin main
```

## Para que yo pueda hacerlo aquí

Necesitaré una de estas dos cosas:

- la URL del repositorio y que GitHub Desktop / credenciales ya estén configuradas en esta máquina
- o permiso y datos suficientes para configurar autenticación

## Despliegue en dedicado

Una vez subido:

```bash
git clone <URL_DEL_REPO>
cd <CARPETA>
cp .env.example .env
# editar .env
alembic upgrade head
python run.py
```

Si el dedicado va por Docker:

```bash
docker compose up --build -d
docker compose exec bot alembic upgrade head
```
