# Weyv-Referenzprüfung und Sonora-Abgleich

Prüfstand: **22. November 2026**. Referenz ist die an diesem Tag öffentlich
angebotene Weyv-Webseite sowie der darüber kostenlos erhältliche Installer.
Dieser Bericht dokumentiert beobachtbare Funktionen und Unterschiede. Er ist
keine Zusage, dass Sonora sämtliche Weyv-Funktionen enthält.

**Vollständige Funktions-/Pixelgleichheit ist nicht hergestellt.** Sonora ist
eine eigenständige GTK-3-Anwendung für Linux. Die aktuellen Änderungen betreffen
insbesondere den schwebenden Visualizer, seine Einstellungen und die Darstellung.
Die Windows-App wurde hier nicht ausgeführt.

## Quellen und untersuchte Datei

- Produktseite: <https://www.weyv.app/>
- Offizieller Download-Endpunkt: <https://www.weyv.app/api/download>
- Sichtbare Produktbedingungen: <https://www.weyv.app/legal.html>
- Vom Installer genannte Release-Ablage: <https://github.com/hollen689/weyv-releases>

| Merkmal | Festgestellter Wert |
| --- | --- |
| Installerdatei | `Weyv-Setup-1.1.23.exe` |
| Produktversion | `1.1.23` |
| Größe | 82.986.464 Bytes |
| SHA-256 | `7455034d256f31c99a08f22833f3ddb58b5e63778ebd9db0ac3f65fad75ce0d8` |
| Verpackung | Windows-NSIS-Installer; darin `app-64.7z`, anschließend Electron mit `resources/app.asar` |

Der Download-Endpunkt kann künftig eine andere Version liefern. Die Angaben
oben identifizieren ausschließlich die tatsächlich untersuchte Datei.

Die Webseite wurde über HTTP gelesen und mit Chromium einschließlich der
interaktiven Ring-Auswahl betrachtet. Der Installer wurde entpackt; Einstellungen,
Dashboardstruktur und Fensterbehandlung wurden **statisch** untersucht. Einzelne
Dashboardansichten wurden zusätzlich in Chromium mit einer simulierten
Electron-Schnittstelle dargestellt. Das bestätigt deren sichtbare Anordnung,
aber weder eine laufende Windows-App noch deren Audio-, Medien- oder
Fensterintegration. Es wurde keine Pro-Aktivierung oder Testphase gestartet.
Dieser Sonora-Quellbaum enthält keine aus Weyv übernommenen Quelltexte oder
Original-Assets.

## Visualizer: genaue Referenzoptionen

Die Webseite nennt sechs Stile. Der untersuchte Installer enthält zusätzlich
**Halo**: Bars, Mirror, Blocks, Ring, Halo, Waveform und Aurora.

| Bereich | Weyv 1.1.23 | Sonora-Abgleich |
| --- | --- | --- |
| Stile | Sieben oben genannte Stile | Entsprechende eigenständige Renderer vorhanden; keine Pixelgleichheit zugesichert |
| Farben | Albumfarben; Nebula, Ember, Mint, Ice, Sunset, Mono; eigene Akzentfarbe | Referenzpaletten, Akzentfarbe und Albumfarben vorhanden; zusätzlich bestehende Sonora-Paletten |
| Dichte | Faktor 0,50–1,60; Schritt 0,05 | Eigene Spektraldichte 16–128; Werte und Analyse stimmen nicht eins zu eins überein |
| Hintergrundfläche | 0–100 %; Schritt 2 %; standardmäßig aus | Eigene Hintergrundfläche vorhanden; Kreisfläche statt identischer Windows-Karte |
| Leuchten | Separat ein-/ausschaltbar | Eigene einstellbare Leuchtstärke |
| Titelanzeige | Separat ein-/ausschaltbar | Titelanzeige und separate Widget-Einstellung vorhanden |
| Albumlicht | Cover als weiches Farbfeld; unabhängig vom Titelstreifen | Eigenständige Albumlicht-Option vorhanden |
| Titellayout | Compact, Centred, Cover, Text only | Entsprechende Layoutauswahl vorhanden; eigene Typografie und Geometrie |
| Fensterlage | Desktop, Always on top, Normal window | Overlay, Normal und X11-Desktop als Linux-Entsprechungen |
| Größen | Small 240×240, Medium 300×300, Large 380×380, Wide 460×220; Banner und Custom | Entsprechende vier Größenpresets, Banner und freie Abmessungen vorhanden; Banner bleibt durch die Linux-Reglergrenzen begrenzt |
| Freie Abmessungen | Dashboardbreite 150–2560, Höhe 150–1440, Schritt 10; App kann Maxima an Display anpassen | Separate gespeicherte Breite 150–2560 und Höhe 150–1440 vorhanden; keine identische dynamische Monitorbegrenzung |
| Deckkraft | 20–100 %; Schritt 2 % | Deckkraft 20–100 % vorhanden |
| Sperren | Position fixiert, Hover-Steuerung ausgeblendet, Klicks gehen hindurch | Sperre und Durchklicken als Widget-Optionen vorhanden |
| Position | Ziehen, Eckgriff zum Skalieren, Position zurücksetzen und Widget zeigen | Verschieben, Eckgriff, Größenregler, Zentrieren und Anzeigen vorhanden; eigenständige Linux-Bedienung |

Der Ring der Referenz hat runde, paarweise gespiegelte Spektralstrahlen, eine
langsame pegelabhängige Drehung, einen weichen farbigen Kern und einen dünnen
inneren Kreis. Bass beeinflusst den Kern, die Außenkanten laufen transparent
aus. **Halo** ist ein eigener Stil mit Spektrum um das Cover und deshalb nicht
mit Ring gleichzusetzen.

Die vier Titelanordnungen unterscheiden sich sichtbar: Compact legt ein kleines
Cover neben zwei Textzeilen; Centred stapelt Cover und zentrierten Text; Cover
vergrößert das Bild; Text only blendet es aus. In Weyv reservieren sie jeweils
54, 96, 78 und 46 Pixel am unteren Rand. Bei gestoppter Wiedergabe entfällt dort
der Titelstreifen. Halo benötigt keinen zusätzlichen Titelstreifen unter seinem
zentralen Cover. Sonoras Platzaufteilung ist eigenständig.

## Overlay und Plattformgrenzen

Weyv unterscheidet drei Modi. Desktop bindet das Widget in Explorers
Wallpaperfenster ein und repariert die Verbindung nach einem Explorer-Neustart.
Always on top verwendet ein nicht aktivierendes Werkzeugfenster auf einer hohen
Topmost-Ebene und prüft regelmäßig, ob es wieder nach oben gesetzt werden muss.
Normal window nimmt am normalen Fensterstapel teil.

Für den gewünschten schwebenden Sonora-Kreis ist **Overlay** die Voreinstellung,
auch bei der Migration alter Einstellungen ohne explizite neue Moduswahl. Eine
bewusst gewählte Einstellung Normal oder Desktop bleibt speicherbar. Die
Implementierung verwendet unter X11 ein nicht vom Fenstermanager verwaltetes
Overlay (`override_redirect`) mit eigener Verschiebe-/Größenbedienung. Normal
und Desktop schalten zurück auf verwaltete Fenster. Der X11-Abnahmetest prüft
die tatsächliche Reihenfolge gegenüber einer separat laufenden Anwendung,
einschließlich Maximierung, Vollbild, „Desktop anzeigen“, Moduswechsel und
erneutem Anzeigen. Das Widget übernimmt dabei keinen Tastaturfokus.

Das ist keine plattformübergreifende Garantie, jedes andere Fenster überlagern zu
können. Die geprüfte X11-Sitzung verwendet Openbox und einen Compositor. Wayland
überlässt die Ebenen dem Compositor; GTK-3-Fensterhinweise ersetzen kein
compositorspezifisches Layer-Shell-Protokoll. Sperrbildschirm, geschützte
Systemoberflächen und exklusive Vollbildpfade sind damit nicht abgedeckt. Eine
Windows-WorkerW-/Topmost-Integration ist in dieser Linux-App nicht vorhanden.

## Weitere kostenlose Referenzbereiche

| Bereich | Weyv-Optionen | Stand gegenüber Sonora |
| --- | --- | --- |
| Audio | Automatic, reines Systemaudio, ausdrücklich gewähltes Mikrofon, Geräteauswahl, Pegelanzeige; Empfindlichkeit 0,40–2,50 in 0,05-Schritten; Glättung 0–0,95 in 0,01-Schritten | Linux-PulseAudio/PipeWire-Monitorausgabe und eigene FFT vorhanden. Andere Analyse, Reglerbereiche und Gerätewechselbehandlung; kein Windows-WASAPI-Helfer |
| Medieninformationen | Titel, Interpret, Cover und Befehle aus der Windows-Mediensitzung; keine Anmeldung für die Anzeige nötig | Linux-MPRIS und optional Soloist; nicht Windows-SMTC. Die Erkennung hängt vom jeweiligen Player ab |
| Uhr | Digital, Stacked, Minimal, Analog, Words; fünf Windows-Schriftfamilien; Größe, Gewicht, Zeichenabstand, Deckkraft, Ausrichtung, Vollfarbe/Zweifarbverlauf/Visualizerfarbe, 12/24 h, Sekunden, Datum lang/kurz, Schatten, Glow, Basspuls, Ebene und Sperre | Fünf grundlegende Darstellungen vorhanden; nicht sämtliche Schrift-, Farb-, Datums-, Animations- und Fensteroptionen gleich umgesetzt |
| Lyrics | Kostenlose Stile Flow, Word reel, Cinematic, Kinetic, Typewriter, Neon, Subtitle; Schrift, Schreibweise, Ausrichtung, Farbe, Bewegung, Schatten/Glow, Basspuls, globale und songspezifische Zeitkorrektur, Ebene und Sperre | Lokale LRC-/Textdateien und optionale manuelle LRCLIB-Suche vorhanden; keine vollständige Stil-, Widget- oder automatische Suchgleichheit |
| Vollbild | Eigene Stil- und Farbwahl, Albumfarben und Hintergrundwahl; Cover, Bassimpulse, Transportsteuerung, ausblendende Bedienung; Space/Pfeile/V/C/Esc | Eigenes Vollbild vorhanden; nicht sämtliche Hintergründe, Effekte und unabhängigen Layoutoptionen gleich umgesetzt |
| System | Autostart; 24/30/60/unbegrenzt FPS; Pause bei Verdeckung; Pause beim Spielen; Leistungsmodus mit 15 FPS; Tastenkürzel; Updateprüfung; Version; Beenden | Autostart, Bildrate, lokale Steuerbefehle und Tray vorhanden. Keine vollständige Verdeckungs-/Spielerkennung oder identische Aktualisierungsinfrastruktur |

Unter Sonora enthalten Spotify-Web-API und Soloist zusätzliche Funktionen und
Abhängigkeiten. Sie ersetzen keine beliebige Windows-Mediensitzung und sind kein
Beleg für Gleichheit des freien Weyv-Funktionsumfangs.

## Pro- und weitere Referenzfunktionen

Der untersuchte Installer ist umfangreicher als die Produktseite. Seine
Dashboardbereiche umfassen zusätzlich zu Visualizer, Audio, Uhr, Lyrics,
Vollbild und System folgende Funktionsfamilien:

- Orb, Vinyl, Waterfall, Scope, Particles, Terrain, Meters, Snow und Murmuration.
- Cassette, CD und Neon-Darstellung.
- Environments, Living wallpaper, Album backdrop, Screen glow, Span und Fluid.
- Eigenständige Now-playing-Karte, Listening stats, Calendar, Disk space,
  Weather und System monitor.
- Cursor trail, Waves und Mood sync.
- Gespeicherte Gesamtprofile mit automatischem Wechsel nach Uhrzeit oder
  geöffneter Anwendung.
- Erweiterte Lyrics-Stile, darunter Stack, Rise, Pop, Karaoke und Scatter.

Diese Bereiche besitzen jeweils weitere Stil-, Farb-, Reaktions-, Größen-,
Monitor- und Platzierungsoptionen. Sie sind **nicht vollständig in Sonora
implementiert**. Sonoras vorhandene Darstellungsprofile entsprechen nicht Weyvs
automatischen Gesamtprofilen. Mehrschichtige Wallpaper, systemweite Zeiger- und
Bildschirmrandeffekte, monitorübergreifende Szenen und vollständige Pro-Widgets
gehören nicht zum hier erreichten Funktionsumfang.

Ein im Installer enthaltener Bildschirmschonerbereich ist im Dashboard verborgen.
Seine statische Existenz bedeutet nicht, dass er als aktuell beworbene oder
praktisch geprüfte Funktion verfügbar ist.

## Aussagekraft der Prüfung

Die Referenzprüfung belegt die genannten Optionen der heruntergeladenen Version.
Sie ersetzt keine praktische Windows-Prüfung. Sonora-Prüfungen dokumentieren
gesondert Linux-Fensterverhalten, Renderer und Einstellungen; sie belegen keine
ungetestete Weyv-Parität. Insbesondere sind eine tatsächliche Weyv-Wiedergabe,
native Windows-Fensterreihenfolge und Pro-Effekte hier nicht praktisch geprüft.

Das Ergebnis ist eine konkret verbesserte Linux-Umsetzung mit dokumentierten
Abweichungen, keine vollständige 1:1-Kopie von Weyv.
