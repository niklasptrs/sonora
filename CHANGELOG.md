# Änderungen

## 0.7.1

- Spread live frequency bands around the full Orbit ring for a more balanced response.

## 0.7.0

- Set up Spotify Soloist from Connections, including download, key storage through Soloist, local playback and optional automatic reconnection.
- Switch between German and English directly in the sidebar.
- English installation and feature guide with screenshots from the application.

## 0.6.1

- Der Desktop-Ring lässt sich dauerhaft abschalten. Titelkarte und Desktop-Lyrics haben eigene Schalter.
- Das Hauptfenster beendet beim Schließen auch Widgets, Aufnahme und Tray.
- Das Lyrics-Feld verarbeitet AltGr-Eingaben über die GTK-Eingabemethode und speichert den Kontakt lokal.
- Die Wiedergabesteuerung verwendet ausschließlich MPRIS und Soloist.

## 0.6.0

- Desktop-Titelkarte mit aktuellem Cover und bis zu drei folgenden Titeln. Unterstützt Soloist, MPRIS-TrackList und Spotify Web API.
- Klick auf den Kreis schaltet die Titelkarte um. Vorheriger Titel, Wiedergabe und nächster Titel sind am Ring erreichbar.
- Separat verschiebbare Lyrics mit gespeicherter Position, Schriftgröße, Zeilenbreite und Ausrichtung.
- Kurze Bassimpulse erzeugen auslaufende Lichtspuren. Coverfarben blenden beim Songwechsel über. Optionales Randlicht lässt Eingaben durch.

## 0.5.3

- Größenpresets merken sich ihre Desktop-Position. Beim ersten Wechsel bleibt die Ringmitte möglichst an derselben Stelle.
- Mausrad und Touchpad scrollen über Reglern und Auswahllisten durch die Einstellungen.
- Wortaufbau, Seitengleiter und Federn stehen im Lyrics-Reiter, im Vollbild und auf der Album-Bühne zur Verfügung.

## 0.5.2

- Seitenwechsel halten den ausgewählten Reiter und den Eingabefokus stabil.
- Textfelder erhalten Tastenkombinationen mit AltGr. Das Lyrics-Kontaktfeld bietet außerdem eine Schaltfläche zum Einfügen von @.

## 0.5.1

- Spotify-Anmeldung öffnet den Standardbrowser einmal pro Anmeldeversuch. Fehler beim Browserstart erscheinen direkt im Dashboard.

## 0.5.0

- Album-Bühne mit transparentem Desktop-Hintergrund, Cover im Spektrum und synchronisierter Liedzeile. Im breiten Format steht die Zeile links, im schmalen Format unter dem Cover.
- Wortweise Hervorhebung für Enhanced-LRC-Dateien. Die optionale LRCLIB-Suche kann während einer Sitzung bei Titelwechseln automatisch laufen.
- Bassimpulse im Vollbild und in der Album-Bühne, langsam wandernder Albumhintergrund im Vollbild und Albumfarben über die C-Taste.
- Doppelten Audio-Reiter entfernt.

## 0.4.1

- Ring mit pegelabhängigem Kernlicht, stärkerer Bassreaktion bei der Drehung und auslaufenden Strahlen.
- Vollbild mit eigener Stil- und Palettenwahl, Albumfarben, drei Hintergründen und optionalen synchronisierten Lyrics. V und C ändern im Vollbild seine eigenen Einstellungen.
- Synchronisierte Lyrics mit einstellbarem Zeilenübergang und Zeitkorrektur von −5 bis +5 Sekunden.

## 0.4.0

- Overlay ist die Standardplatzierung; Fensterlage und Größen bleiben gespeichert. Alte 0.3-Einstellungen werden auf den ausdrücklich gewünschten Overlay-Modus umgestellt.
- Unter X11 bleibt die nicht verwaltete Einblendung auch über aktiven Vollbildfenstern und bei „Desktop anzeigen“ sichtbar. Native Abnahme prüft Fensterreihenfolge und Fokus mit einer separaten Anwendung.
- Breite und Höhe, vier Größenpresets, Banner, Eckgriff und Doppelklick zum Stilwechsel. Schnelle Größenänderungen werden ohne Rücksprung gespeichert.
- Neue Visualizer-Seite mit animierten Stilkacheln, Farbwahl, Live-Vorschau, Größen und Fenstereinstellungen nach dem Aufbau der geprüften Weyv-Oberfläche.
- Halo, Mint und Sunset ergänzt; Hintergrundfläche, Albumlicht und vier Titel-/Cover-Layouts werden tatsächlich gezeichnet. Alle ausgewählten Stile gelten auch für das Desktop-Widget.
- Widget-Sperre lässt Mausklicks durch und blendet die Bedienelemente aus. Entsperren bleibt im Dashboard erreichbar.
- Referenzprüfung von Webseite und Windows-Installer 1.1.23 mit dokumentierten Unterschieden in WEYV-VERGLEICH.md. Keine vollständige Weyv-Funktions- oder Pixelgleichheit.

## 0.3.0

- Dashboard mit überarbeiteter Navigation, zentraler Visualizer-Vorschau und seitlichen Schnellreglern.
- Desktop-Einblendung als transparenter, rahmenloser Kreis; Optionen per Rechtsklick und eingeblendeten Schaltflächen.
- Größe, Position, Farbpalette, Empfindlichkeit, Nachklang, Leuchten, Deckkraft, Albumcover und Fensterebene am Kreis einstellbar.
- Kürzere Audio-Aufnahmeblöcke, überlappende Frequenzanalyse und schneller Ausschlag mit zeitbasiertem Abklingen. Verbesserte Sichtbarkeit leiser Musik; keine künstliche Bewegung bei Stille.
- Bestehende Einstellungen bleiben gültig. Neue Defaults: 60 FPS, 128 Strahlen und angepasste Empfindlichkeit.

## 0.2.0

- Ausgangsversion mit GTK-Dashboard, mehreren Visualizer-Stilen, MPRIS, Spotify Web API, Soloist-Anbindung, Lyrics, Profilen und Desktop-Uhr.
