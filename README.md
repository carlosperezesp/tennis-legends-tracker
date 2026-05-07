# Legend Trajectory Tennis

Una herramienta prototipo para evaluar si un tenista joven está en trayectoria de una "leyenda moderna" comparando sus datos por edad contra jugadores masculinos con 10+ Grand Slams en la era moderna.

## Qué incluye

- `PLAN.md`: metodología, referencias, variables, fórmula y limitaciones.
- `data/legend_trajectory_dataset.json`: estructura de datos por jugador y edad.
- `data/legend_trajectory_dataset.csv`: el mismo dataset en formato CSV.
- `src/legend_trajectory.py`: prototipo simple de cálculo de puntuación y comparación.

## Uso

1. Instala Python 3.11+.
2. Ejecuta el prototipo:

```bash
python3 src/legend_trajectory.py
```

3. Puedes evaluar un jugador específico por nombre:

```bash
python3 src/legend_trajectory.py --player-name "Carlos Alcaraz"
```

4. Puedes cargar un jugador personalizado desde un archivo JSON:

```bash
python3 src/legend_trajectory.py --input-json examples/custom_player_example.json
```

5. También puedes generar un informe HTML:

```bash
python3 src/legend_trajectory.py --player-name "Carlos Alcaraz" --output-html examples/report_alcaraz.html
```

6. También puedes ver ejemplos integrados de Carlos Alcaraz y Jannik Sinner.

## Actualización diaria de partidos

La pestaña `Partidos` consume `data/live_schedule.json`. Para refrescarla una vez al día:

```bash
RAPIDAPI_KEY=tu_clave python3 src/live_schedule_fetcher.py
python3 src/build_index.py --output examples/index.html
```

Hay un workflow en `.github/workflows/update-live-schedule.yml` preparado para ejecutarlo a las 06:15 hora española en verano, usando el secreto `RAPIDAPI_KEY`.

## Qué devuelve

Para un jugador joven, el prototipo muestra:

- `Legend Trajectory Score` (0-100)
- `Categoría` (No leyenda / Multi Slam / Leyenda posible / Territorio leyenda / Leyenda histórica potencial)
- `Arquetipo` más parecido
- Comparación por edad contra leyendas históricas en el dataset
- Proyección conservadora, media y agresiva de Grand Slams
- Gráfica evolutiva en HTML comparando la trayectoria actual, la proyección y el objetivo legendario
- Explicación de limitaciones

## Notas

- El prototipo usa datos incompletos y valores `null` cuando no hay cifras verificables.
- La puntuación es un indicador inicial, no una predicción matemática definitiva.
- Las proyecciones de Grand Slams son heurísticas y se deben validar con datos reales.
