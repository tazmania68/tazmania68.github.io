# Preguntas Frecuentes — Ninja Zenshin Tracker

## General

### ¿Es gratis?
**Sí, 100% gratis y de código abierto.** No hay versiones de pago, ni publicidad, ni rastreo.

### ¿Necesito instalar algo?
Solo **Python 3.8+**. El tracker funciona solo con la librería estándar. Pillow es opcional (solo para generar imágenes).

### ¿Puedo usarlo en el celular?
Sí, de dos formas:
1. **Ejecutándolo en el celular**: Instala [Termux](https://termux.dev/) en Android, instala Python y corre el tracker ahí.
2. **Ejecutándolo en tu PC**: Abre la URL que muestra la terminal (misma WiFi) en el navegador de tu celular. También puedes instalarlo como PWA (añadir a pantalla de inicio).

### ¿Consume muchos recursos?
No. El tracker usa ~50-100 MB de RAM y muy poco CPU. El modo de lectura por defecto (1s) es ligero. Puedes bajar a 30s o 60s si quieres ahorrar batería en un servidor.

---

## Funcionamiento

### ¿Cómo detecta los ataques?
El tracker lee el ranking de clanes cada segundo. Cuando un clan **gana** reputación y otro **pierde** exactamente la misma cantidad (o la suma de varios clanes coincide), se empareja como un ataque: el que ganó atacó al que perdió.

### ¿Por qué a veces no aparece el atacante?
- El atacante podría ser un clan que aún no aparece en el top (el ranking solo muestra los primeros)
- Varios clanes atacaron al mismo tiempo y las cantidades no emparejan exactamente
- El ataque fue muy pequeño (menor al umbral de detección)

### ¿Qué es "Bleed" o "Sangrado"?
Es cuando un clan **pierde reputación de forma continua** durante varios segundos/minutos — normalmente porque varios clanes le están atacando al mismo tiempo, o porque alguien le está haciendo burn constante.

### ¿Qué es el "Top Burn"?
Son los **jugadores específicos** dentro de un clan que están ganando reputación (= atacando). El tracker lee los miembros de cada clan y detecta quién subió puntos.

### ¿Qué son las predicciones?
El tracker calcula el **ritmo** (REP por minuto) de cada clan y proyecta cuál será su reputación y puesto al final de la temporada, asumiendo que mantienen ese ritmo.

---

## Problemas

### El tracker no muestra datos / se queda cargando
1. Verifica que `ninjazenshin.online` esté accesible desde tu navegador
2. Revisa la terminal — si hay errores de conexión, espera a que se recupere solo (tiene reintento automático)
3. Prueba cambiar la URL: `python3 zenshin_tracker.py --url OTRA_URL`

### No recibo alertas por Discord
1. Verifica que el webhook URL esté correcto (pestaña 🔧 Conexión)
2. Comprueba que el "mínimo de puntos" no sea muy alto (prueba con 10)
3. Asegúrate de que el bot de webhook tenga permiso para escribir en el canal
4. Revisa la terminal por errores de Discord

### No recibo alertas por Telegram
1. Asegúrate de haber escrito `/start` a tu bot
2. Verifica que el **chat ID** sea correcto (usa [@userinfobot](https://t.me/userinfobot))
3. El token debe ser completo, incluyendo los dos puntos (`123456:ABC...`)

### Los miembros de los clanes no cargan
- Ve a 🔧 Conexión → usa el **Asistente de conexión** o pega la URL manualmente
- El tracker auto-detecta el endpoint, pero a veces cambia

### Mi historial se perdió
- Los archivos `zenshin_history.json` y `zenshin_series.json.gz` deben estar en la misma carpeta desde donde ejecutas el tracker
- Si moviste el archivo `.py` a otra carpeta, mueve también esos archivos
- Usa **Backup** (en ⚙️ Ajustes) periódicamente para exportar tu configuración

### ¿Puedo correrlo 24/7 en un servidor?
Sí. En Linux puedes usar `nohup`, `tmux`, `screen` o un servicio `systemd`:
```bash
nohup python3 zenshin_tracker.py --poll 2 &
```
Recomendamos `--poll 2` o más lento para un servidor 24/7.

---

## Comunidad

### ¿Cómo contribuyo?
Haz un fork, mejora el código y envía un Pull Request. Todas las contribuciones son bienvenidas.

### Encontré un bug, ¿qué hago?
Abre un **Issue** en GitHub describiendo:
- Qué pasó
- Cómo reproducirlo
- Qué esperabas que pasara
- Captura de la terminal (si hay errores)

### ¿Puedo sugerir features?
¡Claro! Abre un Issue con la etiqueta `enhancement` y describe tu idea.

---

## Legal

### ¿Es legal usar este tracker?
Sí — el tracker solo lee **información pública** del ranking del juego (lo mismo que verías en tu navegador). No modifica el juego, no inyecta nada, no automatiza acciones dentro del juego. Es una herramienta de visualización y alerta.

### ¿Me pueden banear por usarlo?
El tracker no interactúa con tu cuenta del juego — no necesita login, ni token, ni datos de tu cuenta. Solo lee el ranking público. El riesgo es prácticamente nulo. Aun así, úsalo bajo tu propia responsabilidad.

### ¿Por qué es de código abierto?
Para que la comunidad pueda auditarlo, mejorarlo y confiar en que no hace nada raro con tus datos. Todo el código está ahí para que lo revises.
