# Kinematik-Auswertung

Erzeugt mit `python -m wallplotter.kinematics` bzw. `--compare`. Zahlen stammen aus dem Modul, nicht aus dem Kopf — bei geänderter Geometrie einfach neu laufen lassen.

*Neu gerechnet im August 2026:* Die Auflösungsspalte hatte einen Fehler in der
Jacobi-Matrix — es wurden Komponenten aus beiden Spalten gemischt. Richtig ist
`Schrittweite / |sin(Riemenwinkel)|`. Der schlechteste Wert bleibt derselbe, die
Werte dazwischen ändern sich; vor allem wies die alte Rechnung die falsche
Problemzone aus, und `wallplotter.motion` bremste danach am falschen Ende.

## Startgeometrie: Anker 150 mm seitlich, 150 mm über der Fläche

```
WallPlotter-Kinematik
============================================================
Fläche          2000 × 2500 mm
Anker           links x=-150, rechts x=2150, y=2650 mm (Spannweite 2300 mm)
Motor           200 Vollschritte × 16 Mikroschritte, Pulley 20Z/2mm → 40 mm/Umdrehung
Riemenauflösung 80.0 Schritte/mm (12.5 µm pro Schritt)
Gondel          300 g = 2.94 N

Stützstellen (X/Y in mm)
------------------------------------------------------------
        Position       Riemen l/r   Auflösung   Winkel   Zug max
             0/0        2654/3412       19 µm   42.3°     2.8 N
           500/0        2729/3122       17 µm   45.7°     2.2 N
          1000/0        2889/2889       17 µm   46.9°     1.6 N
          1500/0        3122/2729       17 µm   45.7°     2.2 N
          2000/0        3412/2654       19 µm   42.3°     2.8 N
           0/625        2031/2953       16 µm   51.0°     2.8 N
         500/625        2127/2612       15 µm   57.0°     2.2 N
        1000/625        2329/2329       15 µm   59.2°     1.7 N
        1500/625        2612/2127       15 µm   57.0°     2.2 N
        2000/625        2953/2031       16 µm   51.0°     2.8 N
          0/1250        1408/2566       14 µm   63.0°     2.8 N
        500/1250        1544/2164       13 µm   74.6°     2.3 N
       1000/1250        1812/1812       13 µm   78.8°     1.9 N
       1500/1250        2164/1544       13 µm   74.6°     2.3 N
       2000/1250        2566/1408       14 µm   63.0°     2.8 N
          0/1875         789/2285       13 µm   81.1°     2.8 N
        500/1875        1011/1823       13 µm  104.8°     2.8 N
       1000/1875        1387/1387       13 µm  112.0°     2.6 N
       1500/1875        1823/1011       13 µm  104.8°     2.8 N
       2000/1875         2285/789       13 µm   81.1°     2.8 N
          0/2500         212/2155       17 µm  131.0°     3.9 N
        500/2500         667/1657       40 µm  161.8°     9.4 N
       1000/2500        1160/1160       49 µm  165.1°    11.4 N
       1500/2500         1657/667       40 µm  161.8°     9.4 N
       2000/2500         2155/212       17 µm  131.0°     3.9 N

Bewertung
------------------------------------------------------------
* Auflösung unkritisch: schlimmstenfalls 0.049 mm pro Mikroschritt — feiner als jede Stiftspitze.
* Riemen laufen bei 1000/2500 mm bis auf 165° auseinander, also fast in eine Linie — dort ist die Höhe schlecht bestimmt und der Zug steigt. Anker höher setzen hilft mehr als sie zu spreizen.
* Maximale Riemenkraft 11.4 N bei 1000/2500 mm; Motor schafft rechnerisch 71 N → Faktor 6 Reserve.
* Längster Riemen 3.41 m pro Seite — mit Umlenkung und Reserve rund 7.8 m Gesamtbedarf.
```

## Ankerposition im Vergleich

```
Ankerposition im Vergleich
============================================================
Überstand = Anker seitlich neben der Fläche, Höhe = Anker über der Oberkante

 Überstand    Höhe    Auflösung    Winkel   Zug max    Riemen
------------------------------------------------------------
      0 mm  100 mm        63 µm     169°    14.8 N   3.28 m
      0 mm  250 mm        27 µm     152°     6.1 N   3.40 m
      0 mm  400 mm        22 µm      35°     4.0 N   3.52 m
    150 mm  100 mm        72 µm     170°    17.0 N   3.37 m
    150 mm  250 mm        30 µm     155°     6.9 N   3.49 m
    150 mm  400 mm        20 µm     142°     4.5 N   3.61 m
    300 mm  100 mm        82 µm     171°    19.2 N   3.47 m
    300 mm  250 mm        34 µm     158°     7.8 N   3.59 m
    300 mm  400 mm        22 µm     146°     5.0 N   3.70 m

Gelesen wird spaltenweise: kleinere Auflösung ist besser, der Winkel soll möglichst weit weg von 0° und 180° bleiben,
die Zugkraft klein gegen die rund 70 N, die ein Motor am 20Z-Pulley aufbringt.
```
