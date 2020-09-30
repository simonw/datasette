var DROPDOWN_HTML = `<div class="dropdown-menu">
<div class="hook"></div>
<ul>
  <li><a class="dropdown-sort-asc" href="#">Sort ascending</a></li>
  <li><a class="dropdown-sort-desc" href="#">Sort descending</a></li>
  <li><a class="dropdown-facet" href="#">Facet by this</a></li>
</ul>
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
        return paramsToUrl(params);
    }
    function sortAscUrl(column) {
        var params = getParams();
        params.set('_sort', column);
        params.delete('_sort_desc');
        return paramsToUrl(params);
    }
    function facetUrl(column) {
        var params = getParams();
        params.append('_facet', column);
        return paramsToUrl(params);
    }
    function isFacetedBy(column) {
        return getParams().getAll('_facet').includes(column);
    }
    function iconClicked(ev) {
        ev.preventDefault();
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
        /* Only show facet if it's not the first column, not selected, not a PK */
        var isFirstColumn = th.parentElement.querySelector('th:first-of-type') == th;
        if (isFirstColumn || params.getAll('_facet').includes(column) || th.getAttribute('data-is-pk') == '1') {
            facetItem.style.display = 'none';
        } else {
            facetItem.style.display = 'block';
            facetItem.setAttribute('href', facetUrl(column));
        }
        menu.style.position = 'absolute';
        menu.style.top = (menuTop + 6) + 'px';
        menu.style.left = menuLeft + 'px';
        menu.style.display = 'block';
    }
    var svg = document.createElement('div');
    svg.innerHTML = DROPDOWN_ICON_SVG;
    svg = svg.querySelector('*');
    svg.style.display = 'inline-block';
    svg.style.position = 'relative';
    svg.style.top = '1px';
    var menu = document.createElement('div');
    menu.innerHTML = DROPDOWN_HTML;
    menu.style.position = 'absolute';
    menu.style.display = 'none';
    menu = menu.querySelector('*');
    document.body.appendChild(menu);

    var ths = Array.from(document.querySelectorAll('.rows-and-columns th'));
    ths.forEach(th => {
        if (!th.querySelector('a')) {
            return;
        }
        var icon = svg.cloneNode(true);
        icon.addEventListener('click', iconClicked);
        icon.style.cursor = 'pointer';
        th.appendChild(icon);
    });
})();
