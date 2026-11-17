function storyMentionLabel(text) {
    return String(text || "").replace(/\s*\([^)]*\)\s*$/, "").trim();
}

function insertAtCursor(textarea, snippet) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const value = textarea.value;
    const before = value.slice(0, start);
    const after = value.slice(end);
    const padBefore = before.length && !/\s$/.test(before) ? " " : "";
    const padAfter = after.length && !/^\s/.test(after) ? " " : "";
    textarea.value = before + padBefore + snippet + padAfter + after;
    const cursor = (before + padBefore + snippet).length;
    textarea.focus();
    textarea.setSelectionRange(cursor, cursor);
}

function initStoryMentionEditor(options) {
    const textarea = document.getElementById("id_body");
    if (!textarea || typeof initGenviewSelect2 !== "function") {
        return;
    }

    const configs = [
        {
            selector: "#story-mention-person",
            url: options.personUrl,
            placeholder: options.personPlaceholder,
            kind: "person",
        },
        {
            selector: "#story-mention-family",
            url: options.familyUrl,
            placeholder: options.familyPlaceholder,
            kind: "family",
        },
        {
            selector: "#story-mention-place",
            url: options.placeUrl,
            placeholder: options.placePlaceholder,
            kind: "place",
        },
    ];

    configs.forEach(function (cfg) {
        initGenviewSelect2(cfg.selector, cfg.url, cfg.placeholder, { width: "100%" });
        jQuery(cfg.selector).on("select2:select", function (event) {
            const data = event.params && event.params.data;
            if (!data || !data.id) {
                return;
            }
            const label = storyMentionLabel(data.text) || data.text;
            insertAtCursor(textarea, "[" + label + "](" + cfg.kind + ":" + data.id + ")");
            jQuery(this).val(null).trigger("change");
        });
    });
}