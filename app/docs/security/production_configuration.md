# Configuración segura de producción

SOOI separa `config.settings.dev` de `config.settings.prod`. Desarrollo conserva valores locales utilizables; producción fija `DEBUG=False` y falla al importar si faltan valores críticos o son inseguros.

## Variables obligatorias

`DJANGO_SECRET_KEY` debe ser única y tener al menos 50 caracteres. `DJANGO_ALLOWED_HOSTS` exige hosts explícitos, sin comodín, esquema ni ruta. `DJANGO_CSRF_TRUSTED_ORIGINS` exige una lista explícita de orígenes HTTPS. `DB_PASSWORD` también es obligatoria en producción. Los secretos de base de datos y correo deben proceder del gestor de secretos autorizado; `.env.release.example` sólo documenta nombres y valores sintéticos.

## Controles efectivos

- cookies de sesión y CSRF seguras, `HttpOnly` y `SameSite=Lax`;
- redirección HTTPS activa por defecto;
- proxy TLS explícito mediante `X-Forwarded-Proto` y host reenviado configurable;
- HSTS de 31 536 000 segundos, subdominios y preload por defecto;
- `nosniff`, `X-Frame-Options: DENY` y referrer policy restringida;
- Django Axes permanece activo según la configuración común.

Antes de activar HSTS con subdominios/preload, el responsable debe confirmar que todos los subdominios soportan HTTPS. Puede reducirse `DJANGO_SECURE_HSTS_SECONDS` de forma explícita durante una adopción controlada; nunca puede ser negativo.

## Comprobación aislada

Con una imagen local candidata y valores sintéticos:

```sh
IMAGE_TAG=sooi:candidate OUTPUT_DIR=artifacts/verify \
RELEASE_MANIFEST=artifacts/build/release-manifest.json \
SOURCE_COMMIT="$SOURCE_COMMIT" SOURCE_TREE_ID="$SOURCE_TREE_ID" \
SOURCE_ARCHIVE_SHA256="$SOURCE_ARCHIVE_SHA256" SOURCE_DIR="$SOURCE_DIR" \
BASE_IMAGE="$BASE_IMAGE" ops/release/verify_candidate.sh
```

La comprobación usa `--network=none`, no genera ni aplica migraciones y no accede a producción.

## Build reproducible

El contexto se prepara desde un commit explícito, nunca desde el directorio de trabajo:

```sh
SOURCE_COMMIT=$(git rev-parse '<ref>^{commit}')
SOURCE_TREE_ID=$(git rev-parse "$SOURCE_COMMIT^{tree}")
SOURCE_DATE_EPOCH=$(git show -s --format=%ct "$SOURCE_COMMIT")
git archive --format=tar --output source.tar "$SOURCE_COMMIT"
SOURCE_ARCHIVE_SHA256=$(sha256sum source.tar | cut -d' ' -f1)
mkdir source-export
tar -xf source.tar -C source-export
SOURCE_DIR=$PWD/source-export
```

`build_candidate.sh` falla si falta cualquiera de esas entradas, `BASE_IMAGE`, `OUTPUT_TAG` u `OUTPUT_DIR`. Exige una `BASE_IMAGE` ya cargada, usa `--pull=false`, `--network=none` y no instala dependencias. Tanto el Dockerfile como `.dockerignore` se leen del export exacto en `SOURCE_DIR`; el build rechaza un worktree con `.git` y no calcula ni sustituye el commit. El manifiesto registra commit, Git tree ID, SHA-256 del archivo fuente, tag e ID de base, tag e ID candidato, hashes de Dockerfile y `.dockerignore`, y epoch.

`verify_candidate.sh` comprueba el checksum relativo del manifiesto y exige que entradas explícitas, manifiesto, etiquetas OCI e identidades locales coincidan. `verify_reproducible.sh` pasa la misma exportación y los mismos metadatos a ambas reconstrucciones; compara primero la identidad exacta y, si el builder no la conserva, la configuración normalizada y las identidades de capas.

## Higiene de artefactos de respaldo

El artefacto de release no admite archivos de aplicación con patrones
`*.bak`, `*.bak.*`, `*.bak_*`, `*.backup`, `*.backup.*` o `*.backup_*`.

La política se aplica en tres capas:

1. los backups históricos no forman parte del commit candidato;
2. `.dockerignore` impide su entrada futura en el contexto;
3. el Dockerfile elimina y verifica los restos heredados de la imagen base.

`verify_candidate.sh` genera `image-backup-inventory.txt` y bloquea el
candidato si encuentra cualquier coincidencia bajo `/app` u `/opt/sooi`.
