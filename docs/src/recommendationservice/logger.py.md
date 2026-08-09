# `src/recommendationservice/logger.py`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

## Propósito
El archivo `logger.py` proporciona una clase personalizada `CustomJsonFormatter` y una función `getJSONLogger` para manejar el registro de logs en formato JSON. Estas funciones se utilizan para configurar un logger que exporte información de logs en formato JSON.

## Componentes principales

* `CustomJsonFormatter`: Clase que extiende la clase base `jsonlogger.JsonFormatter`. Agrega campos adicionales a los registros de logs, como el timestamp y la severidad.
* `add_fields`: Método dentro de la clase `CustomJsonFormatter` que se encarga de agregar campos a los registros de logs.
* `getJSONLogger`: Función que crea un logger configurado con el formato JSON personalizado. El logger se puede utilizar para exportar información de logs en formato JSON.

## Dependencias
Este archivo depende de las siguientes bibliotecas:
* `logging`
* `sys`
* `pythonjsonlogger`

## Riesgos de deuda técnica
* La clase `CustomJsonFormatter` es duplicada, ya que otras servicios de Python no comparten los módulos de registro de logs. Esto puede ser un problema si se intenta compartir este módulo con otros proyectos.
* La función `add_fields` utiliza hardcodeados para convertir la severidad a mayúsculas si existe. Es posible que sea mejor manejar esta lógica de manera más dinámica.
* No hay comentarios explícitos en el método `add_fields`. Añadir comentarios puede ayudar a otros desarrolladores a entender mejor la lógica detrás de este método.