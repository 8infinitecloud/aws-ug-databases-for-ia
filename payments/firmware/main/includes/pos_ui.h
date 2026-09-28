#pragma once

/* Arranca la interfaz del POS. Se llama una vez, tras inicializar la pantalla. */
void pos_ui_start(void);

/* Muestra el estado de la red en la barra superior. */
void pos_ui_set_wifi(bool connected);
