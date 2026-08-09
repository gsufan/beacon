# `src/emailservice/logger.py`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

## Propósito
Este módulo (`logger.py`) proporciona una forma personalizada de logging para los servicios de correo electrónico, utilizando el formato JSON y personalizando la forma en que se muestran los timestamps y la severidad.

## Componentes principales
* `CustomJsonFormatter` — un formatter personalizado que agrega campos adicionales al registro del logger, como el timestamp y la severidad.
* `add_fields` — una función que se utiliza dentro de `CustomJsonFormatter` para agregar campos al registro.
* `getJSONLogger` — una función que crea un objeto logger con un formato personalizado y devuelve el logger configurado.

## Dependencias
Este archivo depende de:
* `logging` — el módulo de logging de Python
* `sys` — el módulo de sistema para obtener acceso a la salida estándar (stdout)
* `pythonjsonlogger` — un módulo externo para trabajar con formatos JSON en logging

## Riesgos de deuda técnica
Este código tiene algunos problemas:
* La clase `CustomJsonFormatter` se duplica en otros servicios Python, lo que puede ser un problema de repetición y mantenimiento.
* Los valores hardcodeados, como el nivel de logger (INFO) y la propagación de logger (`propagate = False`), pueden requerir ajustes según las necesidades específicas del servicio.