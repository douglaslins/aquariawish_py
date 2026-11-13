Loading panel banner
--------------------
Put your banner in this folder, named banner.png (banner.webp, banner.jpg and banner.jpeg are also read, in that order).
Size: 573 x 230 pixels. It is drawn to the right of the 230 x 230 area with the progress donut.
With no file here, that area shows a plain dark surface.

In the program built with PyInstaller this folder is inside the "_internal" folder. Put the banner in the data folder instead:
  <folder of PullsTracker.exe>\data\assets\loading\banner.png
A banner there is used before the one in this folder (the data folder is created the first time the program starts).

Preview without starting the application: open loading.html?demo=1 in a browser.
