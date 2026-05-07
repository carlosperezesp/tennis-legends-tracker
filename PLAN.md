# PLAN para Legend Trajectory Tennis

## 1. Metodología

1. Definir una referencia histórica de jugadores masculinos con 10+ Grand Slams desde 1990 hasta 2026.
2. Construir un conjunto de variables por edad que capturen:
   - ritmo de Grand Slams
   - rendimiento en los grandes torneos
   - consistencia y fiabilidad
   - dominio de ranking
   - títulos grandes y versatilidad de superficie
   - impacto internacional y longevidad.
3. Normalizar cada medida para que pueda compararse entre jugadores y edades.
4. Crear una fórmula de puntuación compuesta (`Legend Trajectory Score`) con pesos definidos.
5. Generar arquetipos basados en patrones de desarrollo y estilo de carrera.
6. Presentar un prototipo simple que ingrese datos de un jugador joven y devuelva:
   - puntuación
   - categoría
   - arquetipo
   - comparación por edad contra leyendas
   - proyecciones conservadora/media/agresiva
   - limitaciones del modelo.

## 2. Jugadores históricos de referencia

Los jugadores de la "leyenda moderna" definidos para esta fase son:

- Roger Federer
- Rafael Nadal
- Novak Djokovic
- Pete Sampras

También se consideran como puntos de referencia importantes para la era moderna y para comparación por edad:

- Carlos Alcaraz
- Jannik Sinner
- Daniil Medvedev
- Andy Murray (como comparación de carrera moderna aunque no llega a 10 GS)

## 3. Variables que vamos a usar

- `player_name`
- `age`
- `year`
- `grand_slams_total`
- `grand_slam_finals`
- `grand_slam_semifinals`
- `atp_titles_total`
- `atp_250_titles`
- `atp_500_titles`
- `masters_1000_titles`
- `atp_finals_titles`
- `olympic_gold`
- `olympic_silver`
- `olympic_bronze`
- `davis_cup_titles`
- `ranking_end_year`
- `best_ranking_by_age`
- `weeks_at_number_1_by_age`
- `top_10_wins`
- `win_rate`
- `hard_win_rate`
- `clay_win_rate`
- `grass_win_rate`
- `major_injuries_or_interruptions`
- `age_first_grand_slam`
- `age_fifth_grand_slam`
- `age_tenth_grand_slam`
- `age_last_grand_slam`

## 4. Fórmula inicial de scoring

El `Legend Trajectory Score` se calculará como un valor compuesto de 0 a 100:

- `Grand Slam pace` (30%): ritmo de acumulación de Grand Slams por edad y velocidad para alcanzar hitos críticos.
- `Ranking y semanas como nº1 por edad` (20%): rendimiento en ranking y dominio en tramos tempranos.
- `Big titles` (15%): Masters 1000, ATP Finals y JJOO.
- `Títulos ATP 500 y 250` (10%): profundidad de la colección de títulos regulares.
- `Consistencia en Grand Slams` (10%): finales, semifinales y cuartos de final.
- `Dominio contra top 10` (7.5%): victorias ante rivales Top 10.
- `Versatilidad por superficies` (5%): balance de rendimiento en hard, clay y grass.
- `Davis Cup e impacto internacional` (2.5%): títulos por equipos e impacto más allá de los torneos individuales.

La puntuación final será la suma ponderada de cada componente, con cada subcomponente normalizado a un rango base antes de aplicar pesos.

## 5. Limitaciones del modelo

- Datos incompletos o parciales: se usarán `null` u `unknown` donde no haya cifras verificables.
- Sesgo de era: los jugadores modernos tienen más torneos y condiciones diferentes respecto a los 90.
- Enfoque en hombres: no incluye datos de jugadoras ni comparaciones mixtas.
- No mide factores cualitativos: mentalidad, rivalidades, estilo, lesiones internas o adaptaciones tácticas.
- Proyecciones de Grand Slams son estimaciones simplificadas, no predicciones de resultado.
- Se basa en hits históricos y no en modelos estadísticos avanzados de machine learning.

## 6. Fuentes recomendadas para validar datos

- ATP Tour oficial: https://www.atptour.com
- ITF y resultados de Grand Slams: https://www.itftennis.com
- Wikipedia de cada jugador para historial por edad y títulos
- Tennis Abstract y Ultimate Tennis Statistics
- Estadísticas oficiales de cada Grand Slam
- Cobertura histórica de ESPN/Reuters/ATP sobre lesiones y temporadas interrumpidas.
