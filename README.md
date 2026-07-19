# RootNode

![Python](https://img.shields.io/badge/Python-3.13-green.svg)
![Django](https://img.shields.io/badge/Django-5.1.7-green.svg)
[![Bootstrap](https://img.shields.io/badge/Bootstrap-7952B3?logo=bootstrap&logoColor=fff)]
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=fff)]

A simple gedcom based management for family trees based on Django framework and bootstrap. Ready to run in a container.

Main features:

- gedcom file import (web and cli)
- modification of data
- media management
- two factor authorizations
- multi language support
- face detection support (would need [facenode](https://github.com/stevwyman/facenode))

The main driver of initiating this project has been to focus on security. This type of information can be very sensitive and therefore **privacy** needs attention. On the other hand you want to share as much as possible to either help others or to get others information, if they have same.

Therefore we have have images that can be marked as private, but trees can be marked as public. When marked as public visitors can only see information that complies with the following rules:

- do not show birth events within the prior 110 years
- do not show death events within the prior 80 years
- do not show marriage events within the prior 60 years
- do not show individual/families where one of the above rule is a active

The above values can be of course configured.

![individual view](docu/individual_view.png "individual view")

![family view](docu/family_view.png "family view")

![media view](docu/media_view.png "media view")

## usage

```sh
python manage.py import_gedcom pfad/zur/datei.ged --tree-name "Familie Müller"
```

### deployment

Right now the default configuration is based on a local db.sqlite3
You might want to provide an volume to store the database and the stored data,
such as photos or documents.

```yaml
services:
  app:
    image: localhost/rootnode:latest
    container_name: genview
    ports:
      - 8003:8003
    volumes:
      - data:/data/genview:z
    env_file:
      - .env
      
volumes:
  data:
    name: genview_data
```

You can also provide configuration data using an .env file. Currently the following
parameters are supported:

```env
SECRET_KEY=django-insecure-u&zzp&ve-be0i^2ie*y!=3y_k3j_zd9q&yn!)b@g3j1rzy3pa(
ALLOWED_HOSTS=127.0.0.1
DEBUG=True

DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=admin
```

## ToDos

- [ ] use hardened images, i.e. by Red Hat
- 

## some tweaks

### global tree view

we have two different approaches to handle the tree view, here are two different javascript to render the tree either by merging new data, or by replacing. Both are triggered by double-click.

#### replacing

```js
<script type="module">
document.addEventListener('DOMContentLoaded', function () {
    const language     = "{{ LANGUAGE_CODE|lower }}";
    const appName      = "genview";
    const treeId       = {{ tree_id }};
    const startDepth   = 4; // Hier kannst du jetzt ruhig wieder 3 oder 4 nehmen!

    const messageDiv = document.getElementById('chart-message');
    const chartContainer = document.getElementById('FamilyChart');
    let f3Chart = null;

    function showError(msg) {
        messageDiv.innerHTML = `<p class="text-danger">${msg}</p>`;
    }

    function buildUrl(personId, depth) {
        return `/${language}/${appName}/tree/${treeId}/individual/${personId}/json/?max_depth=${depth}`;
    }

    // --- Die Lade-Funktion ersetzt jetzt rigoros die alten Daten ---
    function loadTree(personIdToLoad, depth) {
        messageDiv.innerHTML = '<span class="text-info">{% trans "Zentriere Baum neu..." %}</span>';

        fetch(buildUrl(personIdToLoad, depth), { credentials: 'same-origin' })
            .then(resp => {
                if (!resp.ok) throw new Error(`Server-Antwort ${resp.status}`);
                return resp.json();
            })
            .then(newData => {
                if (newData.error) throw new Error(newData.error);

                messageDiv.innerHTML = ''; 

                // 1. Tabula Rasa: Wir löschen den kompletten alten D3/SVG-DOM!
                // (Unser Doppelklick-Listener überlebt, da er am Container-Div hängt)
                chartContainer.innerHTML = '';

                // 2. Chart komplett neu aufbauen
                f3Chart = f3.createChart('#FamilyChart', newData);
                
                f3Chart.setCardHtml()
                    .setCardDisplay([
                        ["first name", "last name"],
                        ["birthday"]
                    ]);

                // 3. Baum rendern (zentriert sich automatisch auf die root-Person)
                f3Chart.updateTree({initial: true});
                
                // --- PRO-TIPP: Browser-Historie updaten ---
                // Ändert die URL in der Adresszeile ohne die Seite neu zu laden.
                // So kann der Nutzer ein Lesezeichen der Ansicht speichern!
                const newViewUrl = `/${language}/${appName}/tree/${treeId}/individual/${personIdToLoad}/view/`;
                window.history.pushState({ personId: personIdToLoad }, "", newViewUrl);
            })
            .catch(err => {
                console.error(err);
                showError('{% trans "Baum konnte nicht geladen werden." %}<br>' + err.message);
            });
    }

    // --- Interaktion: Rechtsklick (Context Menu) - Ultimative __data__ Methode ---
    chartContainer.addEventListener('contextmenu', function(e) {
        
        // 1. Standard-Menü blockieren
        e.preventDefault();
        e.stopPropagation();

        // 2. Wir starten beim exakt geklickten Element (z.B. dem Namen oder Bild)
        let currentElement = e.target;
        let clickedPersonId = null;

        // 3. Wir klettern den DOM-Baum nach oben, bis wir den Container erreichen
        while (currentElement && currentElement !== chartContainer) {
            
            // D3 speichert seine Magie in der versteckten Eigenschaft '__data__'
            if (currentElement.__data__ && currentElement.__data__.data && currentElement.__data__.data.id) {
                clickedPersonId = currentElement.__data__.data.id;
                break; // Wir haben die ID gefunden, Suche abbrechen!
            }
            
            // Eine Ebene höher gehen
            currentElement = currentElement.parentNode;
        }

        // 4. Wenn wir eine ID gefunden haben, laden wir den Baum neu!
        if (clickedPersonId) {
            console.log("Erfolgreich ID gefunden via __data__:", clickedPersonId);
            loadTree(clickedPersonId, startDepth); 
        } else {
            console.log("Klick war auf den Hintergrund, nicht auf eine Person.");
        }
        
    }, true);

    // --- PRO-TIPP: Browser "Zurück"-Button abfangen ---
    window.addEventListener('popstate', function(event) {
        if (event.state && event.state.personId) {
            loadTree(event.state.personId, startDepth);
        } else {
            // Fallback auf die ursprüngliche Startperson
            loadTree({{ individual_id }}, startDepth);
        }
    });

    // Initialer Ladevorgang beim ersten Seitenaufruf
    // Wir speichern den ersten Zustand direkt in der Browser-Historie
    window.history.replaceState({ personId: {{ individual_id }} }, "");
    loadTree({{ individual_id }}, startDepth);
});
</script>
```

#### merging

```js
<script type="module">
document.addEventListener('DOMContentLoaded', function () {
    const language     = "{{ LANGUAGE_CODE|lower }}";
    const appName      = "genview";
    const treeId       = {{ tree_id }};
    const startDepth   = 2; // Geringe Tiefe ist besser für den wachenden Baum

    const messageDiv = document.getElementById('chart-message');
    const chartContainer = document.getElementById('FamilyChart');
    
    // Globale Variablen für den Merge-Ansatz
    let allTreeData = [];   
    let f3Chart = null;     

    function showError(msg) {
        messageDiv.innerHTML = `<p class="text-danger">${msg}</p>`;
    }

    function buildUrl(personId, depth) {
        return `/${language}/${appName}/tree/${treeId}/individual/${personId}/json/?max_depth=${depth}`;
    }

    // --- Die Merge-Funktion (verschmilzt alte und neue Daten sicher) ---
    function mergeData(newNodes) {
        const dataMap = new Map();
        allTreeData.forEach(node => dataMap.set(node.id, node));

        newNodes.forEach(rawNode => {
            // SICHERHEITS-CHECK: Wir garantieren, dass 'rels' und alle Arrays IMMER existieren!
            const newNode = rawNode;
            if (!newNode.rels) newNode.rels = {};
            newNode.rels.parents = newNode.rels.parents || [];
            newNode.rels.spouses = newNode.rels.spouses || [];
            newNode.rels.children = newNode.rels.children || [];

            if (dataMap.has(newNode.id)) {
                // Die Person existiert schon im Baum
                const existingNode = dataMap.get(newNode.id);
                
                // Auch hier zur Sicherheit prüfen (falls beim Erst-Laden was fehlte)
                if (!existingNode.rels) existingNode.rels = {};
                existingNode.rels.parents = existingNode.rels.parents || [];
                existingNode.rels.spouses = existingNode.rels.spouses || [];
                existingNode.rels.children = existingNode.rels.children || [];
                
                // Hilfsfunktion, die zwei Arrays ohne Duplikate zusammenführt
                const mergeArrays = (arr1, arr2) => Array.from(new Set([...arr1, ...arr2]));
                
                existingNode.rels.parents = mergeArrays(existingNode.rels.parents, newNode.rels.parents);
                existingNode.rels.spouses = mergeArrays(existingNode.rels.spouses, newNode.rels.spouses);
                existingNode.rels.children = mergeArrays(existingNode.rels.children, newNode.rels.children);
            } else {
                // Neue Person!
                allTreeData.push(newNode);
                dataMap.set(newNode.id, newNode);
            }
        });
    }

    // --- Die Lade-Funktion für den Merge ---
    function loadTree(personIdToLoad, depth) {
        messageDiv.innerHTML = '<span class="text-info">{% trans "Lade weitere Verwandte..." %}</span>';

        fetch(buildUrl(personIdToLoad, depth), { credentials: 'same-origin' })
            .then(resp => {
                if (!resp.ok) throw new Error(`Server-Antwort ${resp.status}`);
                return resp.json();
            })
            .then(newData => {
                if (newData.error) throw new Error(newData.error);

                messageDiv.innerHTML = ''; 

                // 1. Neue Daten in das globale Array mischen
                mergeData(newData);

                // 2. Chart initialisieren ODER updaten
                if (!f3Chart) {
                    // Erster Start
                    chartContainer.innerHTML = ''; 
                    f3Chart = f3.createChart('#FamilyChart', allTreeData);
                    
                    f3Chart.setCardHtml()
                        .setCardDisplay([
                            ["first name", "last name"],
                            ["birthday"]
                        ]);

                    f3Chart.updateTree({initial: true});
                } else {
                    // Update: Wir geben f3 das neue, größere Array und lassen es rendern
                    f3Chart.store.state.data = allTreeData;
                    f3Chart.updateTree(); // Das erzeugt die schöne Animation!
                }
            })
            .catch(err => {
                console.error(err);
                showError('{% trans "Baum konnte nicht geladen werden." %}<br>' + err.message);
            });
    }

    // --- Unsere robuste Rechtsklick-Erkennung ---
    chartContainer.addEventListener('contextmenu', function(e) {
        e.preventDefault();
        e.stopPropagation();

        let currentElement = e.target;
        let clickedPersonId = null;

        while (currentElement && currentElement !== chartContainer) {
            if (currentElement.__data__ && currentElement.__data__.data && currentElement.__data__.data.id) {
                clickedPersonId = currentElement.__data__.data.id;
                break; 
            }
            currentElement = currentElement.parentNode;
        }

        if (clickedPersonId) {
            console.log("Rechtsklick! Erweitere Baum ab ID:", clickedPersonId);
            // Wir feuern den API-Call ab, die Daten werden gemerged und D3 zeichnet neue Äste
            loadTree(clickedPersonId, startDepth); 
        }
    }, true);

    // Initialer Ladevorgang
    loadTree({{ individual_id }}, startDepth);
});
</script>
````
