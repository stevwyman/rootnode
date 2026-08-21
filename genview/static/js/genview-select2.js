/** Initialize a Select2 AJAX dropdown for genview API search endpoints. */
function initGenviewSelect2(selector, apiUrl, placeholderText, extraOptions) {
    extraOptions = Object.assign({}, extraOptions || {});
    const quickCreate = extraOptions.quickCreate || null;
    delete extraOptions.quickCreate;

    const base = {
        placeholder: placeholderText,
        minimumInputLength: 2,
        allowClear: true,
        ajax: {
            url: apiUrl,
            dataType: "json",
            delay: 250,
            data: function (params) {
                return { q: params.term };
            },
            processResults: function (data, params) {
                const results = (data && data.results) ? data.results.slice() : [];
                const term = ((params && params.term) || "").trim();
                if (quickCreate && term.length >= 2 && results.length === 0) {
                    results.push({
                        id: "__create__",
                        text: quickCreateEmptyLabel(quickCreate.kind, term),
                        createTerm: term,
                    });
                }
                return { results: results };
            },
        },
        templateResult: function (item) {
            if (item.loading) {
                return item.text;
            }
            if (item.id === "__create__") {
                const span = document.createElement("span");
                span.className = "text-success";
                span.textContent = "+ " + item.text;
                return span;
            }
            return item.text;
        },
    };
    const $el = jQuery(selector);
    $el.select2(Object.assign(base, extraOptions));

    if (quickCreate) {
        $el.on("select2:selecting", function (e) {
            const data = e.params && e.params.args && e.params.args.data;
            if (!data || String(data.id) !== "__create__") {
                return;
            }
            e.preventDefault();
            $el.select2("close");
            openQuickCreateModal(quickCreate.kind, data.createTerm || "", $el);
        });
    }
}

function quickCreateEmptyLabel(kind, term) {
    const quoted = '"' + term + '"';
    if (kind === "place") {
        return "Ort anlegen: " + quoted;
    }
    return "Quelle anlegen: " + quoted;
}

function getCsrfToken() {
    const el = document.querySelector("[name=csrfmiddlewaretoken]");
    if (el && el.value) {
        return el.value;
    }
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
}

function quickCreateKindConfig(kind) {
    if (kind === "source") {
        return {
            modalId: "sourceQuickCreateModal",
            formId: "source-quick-create-form",
            primaryName: "title",
        };
    }
    if (kind === "place") {
        return {
            modalId: "placeQuickCreateModal",
            formId: "place-quick-create-form",
            primaryName: "name",
        };
    }
    return null;
}

let quickCreateTargetSelect = null;

function openQuickCreateModal(kind, term, $select) {
    const cfg = quickCreateKindConfig(kind);
    if (!cfg) {
        return;
    }
    quickCreateTargetSelect = $select;
    const form = document.getElementById(cfg.formId);
    if (form) {
        form.reset();
        hideQuickCreateError(form);
        const primary = form.elements[cfg.primaryName];
        if (primary && term) {
            primary.value = term;
        }
    }
    const modalEl = document.getElementById(cfg.modalId);
    if (!modalEl || typeof bootstrap === "undefined") {
        return;
    }
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    if (form) {
        const primary = form.elements[cfg.primaryName];
        if (primary) {
            window.setTimeout(function () {
                primary.focus();
            }, 200);
        }
    }
}

function hideQuickCreateError(form) {
    const err = form.querySelector(".quick-create-error");
    if (!err) {
        return;
    }
    err.classList.add("d-none");
    err.textContent = "";
}

function showQuickCreateError(form, message) {
    const err = form.querySelector(".quick-create-error");
    if (!err) {
        return;
    }
    err.textContent = message;
    err.classList.remove("d-none");
}

function firstFormErrorMessage(data) {
    if (!data) {
        return "Speichern fehlgeschlagen.";
    }
    if (data.error) {
        return data.error;
    }
    if (data.errors) {
        const parts = [];
        Object.keys(data.errors).forEach(function (key) {
            const val = data.errors[key];
            if (Array.isArray(val)) {
                parts.push(val.join(" "));
            } else if (val) {
                parts.push(String(val));
            }
        });
        if (parts.length) {
            return parts.join(" ");
        }
    }
    return "Speichern fehlgeschlagen.";
}

function applySelect2Choice($el, id, text) {
    if (!$el || !$el.length) {
        return;
    }
    const value = String(id);
    if (!$el.find('option[value="' + value + '"]').length) {
        $el.append(new Option(text, value, true, true));
    }
    if ($el.prop("multiple")) {
        const vals = ($el.val() || []).map(String);
        if (vals.indexOf(value) === -1) {
            vals.push(value);
        }
        $el.val(vals);
    } else {
        $el.val(value);
    }
    $el.trigger("change");
}

function submitQuickCreate(kind, form, cfg) {
    hideQuickCreateError(form);
    const url = form.getAttribute("data-create-url");
    if (!url) {
        showQuickCreateError(form, "Speichern fehlgeschlagen.");
        return;
    }
    const submitBtn = form.querySelector("[type=submit]");
    if (submitBtn) {
        submitBtn.disabled = true;
    }
    fetch(url, {
        method: "POST",
        headers: {
            "X-CSRFToken": getCsrfToken(),
            "X-Requested-With": "XMLHttpRequest",
        },
        body: new FormData(form),
    })
        .then(function (res) {
            const contentType = res.headers.get("content-type") || "";
            if (contentType.indexOf("application/json") === -1) {
                return { ok: false, data: { error: "Speichern fehlgeschlagen." } };
            }
            return res.json().then(function (data) {
                return { ok: res.ok, data: data };
            });
        })
        .then(function (result) {
            if (!result.ok) {
                showQuickCreateError(form, firstFormErrorMessage(result.data));
                return;
            }
            if (quickCreateTargetSelect && result.data && result.data.id != null) {
                applySelect2Choice(
                    quickCreateTargetSelect,
                    result.data.id,
                    result.data.text
                );
            }
            const modalEl = document.getElementById(cfg.modalId);
            if (modalEl && typeof bootstrap !== "undefined") {
                const instance = bootstrap.Modal.getInstance(modalEl);
                if (instance) {
                    instance.hide();
                }
            }
            form.reset();
        })
        .catch(function () {
            showQuickCreateError(form, "Netzwerkfehler. Bitte erneut versuchen.");
        })
        .finally(function () {
            if (submitBtn) {
                submitBtn.disabled = false;
            }
        });
}

function bindQuickCreateForms() {
    ["source", "place"].forEach(function (kind) {
        const cfg = quickCreateKindConfig(kind);
        if (!cfg) {
            return;
        }
        const form = document.getElementById(cfg.formId);
        if (!form || form.dataset.bound === "1") {
            return;
        }
        form.dataset.bound = "1";
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            submitQuickCreate(kind, form, cfg);
        });
    });

    document.querySelectorAll("[data-quick-create-open]").forEach(function (btn) {
        if (btn.dataset.bound === "1") {
            return;
        }
        btn.dataset.bound = "1";
        btn.addEventListener("click", function () {
            const kind = btn.getAttribute("data-quick-create-open");
            const target = btn.getAttribute("data-quick-create-target");
            openQuickCreateModal(kind, "", jQuery(target));
        });
    });
}

jQuery(function () {
    bindQuickCreateForms();
});
