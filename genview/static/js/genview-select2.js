/** Initialize a Select2 AJAX dropdown for genview API search endpoints. */
function initGenviewSelect2(selector, apiUrl, placeholderText, extraOptions) {
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
            processResults: function (data) {
                return { results: data.results };
            },
        },
    };
    jQuery(selector).select2(Object.assign(base, extraOptions || {}));
}
