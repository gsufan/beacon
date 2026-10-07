# Seguridad — limitaciones conocidas

Beacon está pensado para uso local o en red interna, por un desarrollador o equipo — **no** para exponerse directamente a internet:

- **La API no tiene autenticación por defecto.** Cualquiera con acceso de red al puerto puede leer/escribir configuración, registrar proyectos y ver código indexado. Se puede activar una clave simple con la variable de entorno `BEACON_API_KEY` (ver más abajo) — sirve para no dejarlo abierto a cualquiera en la misma red, pero no reemplaza un proxy de autenticación real si lo vas a exponer más allá de `localhost`.
- **Sin rate limiting.** No hay límite de frecuencia en `sync`/`query`; en uso interno normal no es un problema, pero no está pensado para tráfico público.
- El explorador de carpetas (`/system/browse-dirs`) está acotado al directorio home del usuario del proceso, y el clonado de repos valida el esquema de la URL (solo http(s)/ssh) — pero ambos asumen que quien llega a la API ya es de confianza, dado el punto anterior.
- `/system/available-models?ollama_host=...` hace que el servidor consulte el host indicado (para poblar la lista de modelos en Configuración). Con la API abierta, eso permite usar a Beacon para hacer peticiones HTTP hacia otras máquinas de su red; otra razón para activar `BEACON_API_KEY` si Beacon no corre solo en `localhost`.

## Activar la clave de API (opcional)

```bash
# Sin Docker
BEACON_API_KEY=algo-secreto beacon serve      # Linux/Mac
$env:BEACON_API_KEY="algo-secreto"; beacon serve   # PowerShell

# Con Docker: crea un archivo .env junto a docker-compose.yml
echo "BEACON_API_KEY=algo-secreto" > .env
docker compose up -d
```

Con la variable seteada, todos los endpoints de datos/configuración exigen el header `X-API-Key`. La UI web te deja ingresar la misma clave en **Configuración > Seguridad** (se guarda solo en tu navegador, vía `localStorage`). Sin la variable seteada (default), la API queda abierta como hasta ahora.
