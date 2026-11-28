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

Cada persona tiene un rol en cada proyecto: los de Scrum y los del resto de la empresa, que usan Beacon sin cambiar sus herramientas.

| Rol | Puede |
|---|---|
| Desarrollador | Preguntar al código, leer la documentación, ver sus issues y la actividad del equipo, usar «¿Por dónde empiezo?», el plan de implementación, «¿Qué probar?» y el radio de impacto |
| QA | Lo mismo que el desarrollador; su herramienta principal es «¿Qué probar?» |
| Soporte, Producto y Observador | Preguntar y leer la documentación; las respuestas llegan en lenguaje simple, sin código, con los archivos de referencia al final |
| Seguridad | Preguntar, ver la auditoría del proyecto y el Panorama |
| Líder técnico | Lo del desarrollador, corregir y verificar la documentación y las respuestas, y ver el Panorama |
| Scrum Master | Lo del desarrollador, sincronizar el proyecto, ver su auditoría y el Panorama |
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

El botón «Borrador del daily» arma el daily de la persona en tres secciones (ayer, hoy y bloqueos) con sus commits desde
el último día hábil (el viernes, si es lunes), locales y en GitHub, y sus issues abiertas; no agrega trabajo que no
aparezca en esos datos y se puede copiar o compartir en Slack.

Para ver «mis issues», cada persona anota su usuario de GitHub (en Mi trabajo o en Equipo > Personas). Sin token de
GitHub en la organización, el Hub solo lee repositorios públicos, con el límite de consultas de GitHub.

## Valoraciones de las respuestas

Debajo de cada respuesta del chat, cualquier persona puede marcar si le sirvió. Un pulgar abajo pide, opcionalmente, qué
está mal o qué falta. Las valoraciones se guardan en Beacon Local sin el nombre de quién votó y llegan al líder técnico
en Panorama > Valoraciones: «Para corregir» (pulgar abajo, con los comentarios) y «Útiles: candidatas a verificar»
(pulgar arriba), ordenadas por cantidad de votos. Al corregir y verificar una respuesta, o verificarla tal como está,
Beacon la ofrece primero a quien pregunte algo parecido y cierra las valoraciones abiertas de esa pregunta; también
se puede descartar una valoración. Cada Beacon Local guarda las suyas, así que la cola reúne las de quienes usan el
mismo servidor.

## Integraciones del equipo

Se configuran en Equipo > Integraciones (solo el administrador); las credenciales quedan en el Hub y nunca llegan a
los navegadores.

| Herramienta | Qué hace en Beacon | Qué se necesita |
|---|---|---|
| GitHub | Backlog del proyecto (issues abiertas, asignadas a cada persona) y actividad de commits; comentar en una issue | Repositorio vinculado al proyecto; token de la organización (lectura, y escritura en issues para comentar) |
| Jira Cloud | Backlog del proyecto desde Jira (si el proyecto tiene una clave de Jira, se usa en lugar de GitHub); comentar en una issue | Sitio `https://<sitio>.atlassian.net`, correo y token de API; clave del proyecto (por ejemplo, `BEAC`); cada persona anota su correo o accountId de Jira |
| Slack | Avisos del equipo (alguien se unió, cambió un rol, se agregó o sincronizó un proyecto, se contrató el plan) y «Compartir en Slack» de una respuesta | Un webhook entrante (`https://hooks.slack.com/services/...`), que elige el canal |

Desde una respuesta de «¿Por dónde empiezo?» se puede **comentar en la issue** (GitHub o Jira) o **compartir en
Slack**; ambas piden confirmación, porque envían la respuesta y los archivos de referencia (no el código) a esa
herramienta, y quedan en la auditoría. Beacon no envía nada a Slack por su cuenta salvo los avisos del equipo, que
no incluyen preguntas ni código.

**Quién conoce estos archivos.** Junto a cada respuesta, Beacon Local muestra quiénes cambiaron más los archivos
citados, leído del historial de git del repositorio (nombres y número de commits, sin correos). Requiere un clon con
historial; una copia sin historial muestra solo quien la importó.

**Ver el trabajo de otra persona.** En Mi trabajo, «Ver el trabajo de…» muestra las issues asignadas a un integrante
del proyecto y sus commits recientes: sirve para retomar su trabajo, por ejemplo, si salió de vacaciones.

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
