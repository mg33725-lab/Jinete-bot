# ⚔️ Bot de Discord para ArgenMU — Control de Drop y Puntos

Bot de Discord diseñado para automatizar el reparto del drop (**Darks**), realizar sorteos transparentes y con compensación por mala suerte, auditar deudas acumuladas y reconocer integrantes de party mediante capturas de pantalla (OCR) o texto.

---

## 🎯 Reglas del Sistema

* **Valor:** Cada **Dark = 10 puntos**.
* **Puntos Fijos:** Se dividen equitativamente entre los participantes:
  $$\text{Puntos Fijos por PJ} = \lfloor\text{Puntos Totales} \div \text{Cantidad de PJs}\rfloor$$
* **Puntos Sobrantes (Sorteo):**
  $$\text{Sobrantes} = \text{Puntos Totales} \pmod{\text{Cantidad de PJs}}$$
  * Los sobrantes se sortean a razón de **1 punto extra** para personas distintas.
  * Si hay más personas que puntos (ej. 1 Dark = 10 puntos entre 15 personas), los puntos fijos son 0 y se sortean 10 puntos entre 10 personas distintas (5 se quedan sin sumar).
* **Compensación por Mala Suerte (Antirracha):**
  * Cada jugador tiene una racha registrada en la base de datos de eventos seguidos en los que no ganó sorteo.
  * El bot genera una tirada transparente visible para todos:
    $$\text{Tirada} = \text{d100 (1 a 100)} + (\text{Racha} \times 8)$$
  * Los puntajes más altos se llevan el punto extra y sus rachas se resetean. Los que no ganaron aumentan su racha para el próximo evento.
* **Cobro de Darks (10 puntos = 1 Dark):**
  * Al llegar a 10 puntos, el sistema marca que el jugador tiene 1 Dark para cobrar.
  * Si un jugador tiene 15 puntos, se le debe 1 Dark y le quedan 5 puntos acumulados.
  * Los administradores pueden consultar `/deudas` y registrar la entrega en el juego con `/pagar`.

---

## 🚀 Guía de Instalación y Puesta en Marcha

### Paso 1: Crear la Aplicación en Discord Developer Portal
1. Ingresá a [Discord Developer Portal](https://discord.com/developers/applications).
2. Hacé clic en el botón **New Application** arriba a la derecha. Ponéle nombre (por ejemplo: `ArgenMU Bot`).
3. En el menú lateral izquierdo, andá a la sección **Bot**:
   * Hacé clic en **Reset Token** para generar el Token del bot.
   * **Copia el Token**.
   * Más abajo, en la sección **Privileged Gateway Intents**, activá:
     * `SERVER MEMBERS INTENT`
     * `MESSAGE CONTENT INTENT`
   * Guardá los cambios (**Save Changes**).

### Paso 2: Invitar el Bot a tu Servidor de Discord
1. En el menú lateral izquierdo, andá a **OAuth2** ➔ **URL Generator**.
2. En **SCOPES**, tildá:
   * `bot`
   * `applications.commands`
3. En **BOT PERMISSIONS** (abajo), tildá:
   * `Send Messages`
   * `Embed Links`
   * `Attach Files`
   * `Read Message History`
   * `Use Slash Commands`
4. Al final de la página se generará un enlace (URL). Copialo, pegalo en tu navegador y seleccioná tu servidor de Discord para invitar al bot.

### Paso 3: Configurar el Token en el Proyecto
1. En esta carpeta (`Bot discord`), creá un archivo llamado `.env` (o renombrá `.env.example` a `.env`).
2. Abrí `.env` y pegá tu token:
   ```env
   DISCORD_TOKEN=tu_token_aqui
   ```

### Paso 4: Iniciar el Bot
Podés iniciarlo de dos formas:
* Haciendo **doble clic en `iniciar_bot.bat`**.
* O desde una terminal ejecutando:
  ```bash
  python bot.py
  ```

---

## 💬 Modo Conversacional con IA (¡Escribile como a una persona!)

Podés hablarle al bot mencionándolo (`@Jinete Bot`) en cualquier canal o mandarle la foto del drop:

* **Puntos de todos:**
  > *"@Jinete Bot haceme la lista con los puntos que tiene cada uno acumulados"*
* **Consultar un PJ:**
  > *"@Jinete Bot cuántos puntos tiene masterr"*
* **Historial de eventos de un PJ:**
  > *"@Jinete Bot qué eventos jugó masterr"* o *"@Jinete Bot historial de Buferit4"*
* **Pagar Darks (descuenta 10 pts por Dark):**
  > *"@Jinete Bot ya le pagué una dark a masterr"* (descuenta 10 pts automáticamente)
* **Cargar quién tiene las Darks físicas (Baúl / Custodia):**
  > *"@Jinete Bot cargá que masterr tiene 3 darks"*
  > *"@Jinete Bot sumale 1 dark a Buferit4"*
  > *"@Jinete Bot quién tiene las darks?"*
* **Consultar deudas (quién tiene para cobrar):**
  > *"@Jinete Bot a quién hay que pagarle darks?"*
* **Girar Ruleta Animada (Minijuego / Sorteo):**
  > *"@Jinete Bot girá una ruleta entre masterr, Buferit4, Hayiku, Sparks"*
* **Cargar evento con o sin foto:**
  > *"BC de las 20:30 cayeron 2 darks"* (con la foto de la party adjunta)

---

## 🎮 Comandos de Barra Diagonal (Slash Commands)

| Comando | Descripción | Ejemplo de Uso |
| :--- | :--- | :--- |
| `/evento` | Registra evento ganado por texto o subiendo 1 o 2 capturas de partys. Incluye ruleta animada. | `/evento nombre:Frozen darks:2 pjs:Titan, ElfaBuff` |
| `/ruleta` | Gira una ruleta animada en GIF entre los personajes ingresados. | `/ruleta participantes:masterr, Buferit4, Sparks` |
| `/baul` | Muestra quiénes tienen en custodia las darks físicas del clan. | `/baul` |
| `/custodia` | Registra o actualiza cuántas darks tiene guardadas un miembro. | `/custodia personaje:masterr darks:3` |
| `/deudas` | Lista los jugadores con 10 o más puntos pendientes de cobro. | `/deudas` |
| `/pagar` | Registra la entrega en el juego y descuenta 10 puntos por Dark. | `/pagar personaje:masterr darks:1 nota:Baúl 1` |
| `/cargar_puntos_manual` | Carga o suma una lista manual de personajes con sus puntos. | `/cargar_puntos_manual lista:Titan:12, masterr:8 modo:Sumar` |
| `/ranking` | Muestra la tabla de clasificación de puntos y participación. | `/ranking` |
| `/perfil` | Ficha detallada con puntos, darks, racha y asistencias de un PJ. | `/perfil personaje:Buferit4` |
| `/eliminar_pj` | Elimina un personaje de la base de datos (por error o prueba). | `/eliminar_pj personaje:Player_1` |
| `/ajustar_puntos` | Modifica manualmente los puntos actuales de un personaje. | `/ajustar_puntos personaje:masterr puntos:10` |
| `/vincular` | Vincula tu usuario de Discord con tu personaje in-game. | `/vincular personaje:Buferit4` |

---

## 📁 Archivos del Proyecto

* `bot.py`: Código principal del bot de Discord, interfaz con botones y comandos Slash.
* `database.py`: Gestor de base de datos SQLite (`argenmu.db`) con histórico de puntos, eventos y pagos.
* `logic.py`: Algoritmo matemático de reparto y tiradas transparentes con compensación de racha.
* `ocr_scanner.py`: Módulo inteligente con EasyOCR para reconocer los nombres de las partys desde capturas de pantalla.
* `iniciar_bot.bat`: Acceso directo para encender el bot en Windows con doble clic.
* `test_suite.py`: Batería de pruebas automatizadas que verifican el 100% de la lógica.
