var DROPDOWN_HTML = `<div class="dropdown-menu">
<div class="hook"></div>
<ul>
  <li><a class="dropdown-sort-asc" href="#">Sort ascending</a></li>
  <li><a class="dropdown-sort-desc" href="#">Sort descending</a></li>
  <li><a class="dropdown-facet" href="#">Facet by this</a></li>
  <li><a class="dropdown-not-blank" href="#">Show not-blank rows</a></li>
</ul>
<p class="dropdown-column-type"></p>
</div>`;

var DROPDOWN_ICON_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="3"></circle>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
</svg>`;

(function() {
    // Feature detection
    if (!window.URLSearchParams) {
        return;
    }
    function getParams() {
        return new URLSearchParams(location.search);
    }
    function paramsToUrl(params) {
        var s = params.toString();
        return s ? ('?' + s) : '';
    }
    function sortDescUrl(column) {
        var params = getParams();
        params.set('_sort_desc', column);
        params.delete('_sort');
        params.delete('_next');
        return paramsToUrl(params);
    }
    function sortAscUrl(column) {
        var params = getParams();
        params.set('_sort', column);
        params.delete('_sort_desc');
        params.delete('_next');
        return paramsToUrl(params);
    }
    function facetUrl(column) {
        var params = getParams();
        params.append('_facet', column);
        return paramsToUrl(params);
    }
    function notBlankUrl(column) {
        var params = getParams();
        params.set(`${column}__notblank`, '1');
        return paramsToUrl(params);
    }
    function isFacetedBy(column) {
        return getParams().getAll('_facet').includes(column);
    }
    document.body.addEventListener('click', (ev) => {
        /* was this click outside the menu? */
        var target = ev.target;
        while (target && target != menu) {
            target = target.parentNode;
        }
        if (!target) {
            menu.style.display = 'none';
        }
    });
    function iconClicked(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var th = ev.target;
        while (th.nodeName != 'TH') {
            th = th.parentNode;
        }
        var rect = th.getBoundingClientRect();
        var menuTop = rect.bottom + window.scrollY;
        var menuLeft = rect.left + window.scrollX;
        var column = th.getAttribute('data-column');
        var params = getParams();
        var sort = menu.querySelector('a.dropdown-sort-asc');
        var sortDesc = menu.querySelector('a.dropdown-sort-desc');
        var facetItem = menu.querySelector('a.dropdown-facet');
        var notBlank = menu.querySelector('a.dropdown-not-blank');
        if (params.get('_sort') == column) {
            sort.style.display = 'none';
        } else {
            sort.style.display = 'block';
            sort.setAttribute('href', sortAscUrl(column));
        }
        if (params.get('_sort_desc') == column) {
            sortDesc.style.display = 'none';
        } else {
            sortDesc.style.display = 'block';
            sortDesc.setAttribute('href', sortDescUrl(column));
        }
        /* Only show facet if it's not the first column, not selected, not a single PK */
        var isFirstColumn = th.parentElement.querySelector('th:first-of-type') == th;
        var isSinglePk = (
            th.getAttribute('data-is-pk') == '1' &&
            document.querySelectorAll('th[data-is-pk="1"]').length == 1
        );
        if (isFirstColumn || params.getAll('_facet').includes(column) || isSinglePk) {
            facetItem.style.display = 'none';
        } else {
            facetItem.style.display = 'block';
            facetItem.setAttribute('href', facetUrl(column));
        }
        /* Show notBlank option if not selected AND at least one visible blank value */
        var tdsForThisColumn = Array.from(
            th.closest('table').querySelectorAll('td.' + th.className)
        );
        if (
            params.get(`${column}__notblank`) != '1' &&
            tdsForThisColumn.filter(el => el.innerText.trim() == '').length
        ) {
            notBlank.style.display = 'block';
            notBlank.setAttribute('href', notBlankUrl(column));
        } else {
            notBlank.style.display = 'none';
        }
        var columnTypeP = menu.querySelector('.dropdown-column-type');
        var columnType = th.dataset.columnType;
        var notNull = th.dataset.columnNotNull == 1 ? ' NOT NULL' : '';

        if (columnType) {
            columnTypeP.style.display = 'block';
            columnTypeP.innerText = `Type: ${columnType.toUpperCase()}${notNull}`;
        } else {
            columnTypeP.style.display = 'none';
        }
        menu.style.position = 'absolute';
        menu.style.top = (menuTop + 6) + 'px';
        menu.style.left = menuLeft + 'px';
        menu.style.display = 'block';
    }
    var svg = document.createElement('div');
    svg.innerHTML = DROPDOWN_ICON_SVG;
    svg = svg.querySelector('*');
    svg.classList.add('dropdown-menu-icon');
    var menu = document.createElement('div');
    menu.innerHTML = DROPDOWN_HTML;
    menu = menu.querySelector('*');
    menu.style.position = 'absolute';
    menu.style.display = 'none';
    document.body.appendChild(menu);

    var ths = Array.from(document.querySelectorAll('.rows-and-columns th'));
    ths.forEach(th => {
        if (!th.querySelector('a')) {
            return;
        }
        var icon = svg.cloneNode(true);
        icon.addEventListener('click', iconClicked);
        th.appendChild(icon);
    });
})();
