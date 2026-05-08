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

## Actualización automática

El dashboard se puede refrescar entero una vez al día con GitHub Actions. El workflow
principal es `.github/workflows/update.yml` y hace tres cosas:

- descarga el calendario ATP masculino de hoy y mañana en `data/live_schedule.json`;
- actualiza perfiles vivos de jugador en `data/player_live_profiles.json`
  separando record oficial disponible, temporada y muestra con estadísticas;
- refresca rankings y CSVs de partidos de Jeff Sackmann sin usar caché vieja;
- regenera `examples/index.html` y commitea los cambios si los hay.

El calendario intenta primero la programación oficial de ATP Tour para los torneos
configurados, y usa RapidAPI como respaldo. Para activar ese respaldo, añade
`RAPIDAPI_KEY` como secreto del repo en GitHub:

`Settings → Secrets and variables → Actions → New repository secret`

En local también puedes crear un `.env.local` no versionado con:

```bash
RAPIDAPI_KEY=tu_clave
```

Los fetchers lo leen automáticamente.

El workflow está programado para ejecutarse a las 06:00 hora española. GitHub usa
cron en UTC, así que hay dos disparos (`04:00` y `05:00` UTC) y un guard interno
que solo deja continuar el run cuando en Madrid son las 06:00.

Actualización manual equivalente:

```bash
RAPIDAPI_KEY=tu_clave python3 src/live_schedule_fetcher.py
RAPIDAPI_KEY=tu_clave python3 src/live_profile_fetcher.py --top 200 --no-cache
python3 src/build_index.py --top 200 --no-cache --output examples/index.html
```

Si la API de partidos falla, el fetcher no pisa el calendario bueno con uno vacío:
el build continúa con el último `data/live_schedule.json` disponible.
El fetcher de perfiles también funciona sin API: genera una capa derivada desde
Jeff Sackmann y, cuando hay `RAPIDAPI_KEY`, intenta enriquecerla con proveedores
RapidAPI. El dashboard usa esa capa para no confundir partidos oficiales
disponibles con partidos que tienen estadísticas profundas.

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
