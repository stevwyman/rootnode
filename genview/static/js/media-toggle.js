document.addEventListener("DOMContentLoaded", () => {
    // Delegiertes Event-Handling – funktioniert auch nach späterem Nachladen von Zeilen
    document.body.addEventListener("click", async (e) => {
        const btn = e.target.closest(".toggle-category");
        if (!btn) return;          // Klick war nicht auf einem Toggle-Button

        const mediaId = btn.dataset.id;
        if (!mediaId) return;

        // UI-Feedback während des Requests
        btn.disabled = true;
        btn.classList.add("spinner-border", "spinner-border-sm");

        try {
            const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            const response = await fetch(TOGGLE_CATEGORY_URL, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify({ id: mediaId })
                        });

            const data = await response.json();

            if (!response.ok || data.error) {
                alert(data.error || "Unerwarteter Fehler");
                return;
            }

            // Icon wechseln – hier simple Text-Icons, du kannst FontAwesome-Klassen nutzen
            btn.innerHTML = data.new_category === "PHOTO"
                ? "📷 → 📄"
                : "📄 → 📷";

        } catch (err) {
            console.error(err);
            alert("Netzwerk-Fehler – bitte später erneut versuchen.");
        } finally {
            // UI-Reset
            btn.disabled = false;
            btn.classList.remove("spinner-border", "spinner-border-sm");
        }
    });
});