[English](../en/team-mode.md) | **Español**

# Modo equipo: Beacon Hub y Beacon Local

Beacon tiene dos partes:

| | Beacon Hub (nube) | Beacon Local (equipo o servidor de la organización) |
|---|---|---|
| Qué hace | Cuentas, organización, roles Scrum por proyecto, licencia y prueba gratuita, auditoría y conexión con GitHub | Indexa el código, responde preguntas con fuentes y genera la documentación con modelos locales |
| Qué guarda | Solo metadatos: personas, roles, nombres de proyectos, eventos de auditoría y el token de GitHub de la organización | El código, el índice y la documentación |

El código y las preguntas nunca viajan al Hub. Beacon Local verifica la sesión y la licencia con la clave pública del
Hub en cada solicitud, sin consultarlo. Sin un Hub configurado, Beacon funciona solo (modo independiente), como antes.

## Roles

Cada persona tiene un rol Scrum en cada proyecto:

| Rol | Puede |
|---|---|
| Desarrollador | Preguntar al código, leer la documentación, ver sus issues y la actividad del equipo, usar «¿Por dónde empiezo?» |
| Scrum Master | Lo anterior, sincronizar el proyecto y ver su auditoría |
| Product Owner | Lo anterior, gestionar quién participa en el proyecto y vincular su repositorio de GitHub |
| Administrador de la organización | Todo: personas, proyectos, plan, servidor de modelos, espacio en disco, y borrar proyectos |

## Licencia y prueba

Al crear la organización empieza una prueba de 30 días con todas las funciones. Después se contrata un plan por
usuario y mes. La licencia viaja firmada por el Hub; si vence, Beacon Local bloquea las consultas y la documentación
hasta renovarla (la página Equipo sigue disponible para el administrador). En el prototipo, el pago es simulado y
queda registrado en la auditoría.

## «Mi trabajo» y «¿Por dónde empiezo?»

Con el repositorio de GitHub vinculado al proyecto, la página Mi trabajo muestra las issues asignadas a la persona (o
todas las abiertas) y los últimos commits del equipo, con su autor y las issues que mencionan. El botón «¿Por dónde
empiezo?» toma el título y la descripción de una issue y pregunta al código qué archivos y funciones revisar
primero; la respuesta cita sus fuentes como cualquier otra.

Para ver «mis issues», cada persona anota su usuario de GitHub (en Mi trabajo o en Equipo > Personas). Sin token de
GitHub en la organización, el Hub solo lee repositorios públicos, con el límite de consultas de GitHub.

## Puesta en marcha

### Con Docker Compose (Hub y Beacon en el mismo equipo)

```bash
docker compose -f docker-compose.yml -f docker-compose.equipo.yml up -d --build
```

Abre http://localhost:8000, elige «Crear organización» y sigue con Equipo > Proyectos para agregar los proyectos y
sus repositorios.

### Sin Docker

```bash
beacon-hub --port 8100 --data hub-data      # el Hub
```

En `config/config.yaml` de cada Beacon Local:

```yaml
hub:
  url: http://servidor-del-hub:8100
```

La primera vez que Beacon se conecta, guarda en esa misma sección la clave pública del Hub (`public_key`); desde ahí
no acepta otra sin que se cambie a mano. También se puede fijar con las variables `BEACON_HUB_URL` y
`BEACON_HUB_PUBLIC_KEY`.

## Auditoría

El Hub registra quién hizo qué y cuándo: inicios de sesión, invitaciones, roles, proyectos, plan, y desde Beacon
Local las consultas, sincronizaciones, borrados y cambios del servidor de modelos. No se guarda el texto de las
preguntas ni el código. El administrador ve toda la organización; el Product Owner y el Scrum Master, sus proyectos.
