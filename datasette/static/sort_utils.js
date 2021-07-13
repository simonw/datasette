function sort() {
  /*
    Generate url with sorting parameters according to the 
    sorting queue
  */
  let sq = window.sorting_queue;
  if (!sq.length) {
    if (!document.getElementById(`alert_cols`))
      document
        .getElementById("sort_utils")
        .insertAdjacentHTML(
          "afterend",
          `<p id='alert_cols'>You need to select some columns to sort!</p>`
        );
    return;
  }
  var sort_url = new URLSearchParams(window.search);
  for (let option of sq) {
    sort_url.append(
      `_sort${option.direction === "dsc" ? "_desc" : ""}`,
      option.name
    );
  }
  //Taken from table.js - line 37
  window.location.href = sort_url ? "?" + sort_url : location.pathname;
}

function toggleSortMenu() {
  /*
    Function used to toggle the visibility of the sorting menu
  */
  let menu = document.getElementById("sort_menu");
  let btn = document.getElementById("toggle_sort_menu");
  if (menu.style.display == "none") {
    menu.style.display = "inline-block";
    menu.classList.add("anim-scale-in");
    //Taken from table.js - lines 79-85
    document.addEventListener("click", function (ev) {
      var target = ev.target;
      while (target && target != menu && target != btn) {
        target = target.parentNode;
      }
      if (!target) {
        menu.style.display = "none";
      }
    });
  } else {
    menu.style.display = "none";
  }
}

function populateSortMenu() {
  window.sorting_queue = [];
  var sort_url = new URLSearchParams(window.location.search);
  /*
    Clearing all of the checkboxes and selecting them according to url parameters.
  */
  var all_checkboxes = document.querySelectorAll(`input[type=checkbox]`);
  for (let checkbox of all_checkboxes) {
    checkbox.checked = false;
  }
  params = [];
  sort_url.forEach(function (value, key) {
    params.push({
      name: key,
      value: value,
    });
    modifySortingQueue(value, key.includes("_desc") ? "dsc" : "asc");
  });
  if (!params.length) return;
  for (let param of params) {
    let chb = document.getElementsByName(param.value)[0];
    chb.checked = true;
    if (param.name.includes("_desc")) var rdb = `${param.value}_dsc`;
    else var rdb = `${param.value}_asc`;
    document.getElementById(rdb).checked = true;
  }
}

function modifySortingQueue(column, type = undefined) {
  /*
    Function that runs every time a checkbox is clicked.
    If it does not exist in the queue, it is added.
    If it exists, it is removed from the queue.
  */
  let sq = window.sorting_queue;
  var s_option = sq.find((condition) => condition["name"] === column);
  if (!s_option) {
    var type =
      document.querySelector(`input[name="${column}_direction"]:checked`)
        .value || type;
    if (!type) type = "asc";
    sq.push({
      name: column,
      direction: type,
      selected: true,
    });
  } else
    sq.splice(
      sq.findIndex(function (e) {
        return e === s_option;
      }),
      1
    );
  displaySortingDescription();
}

function displaySortingDescription() {
  /*
    Function that generates a human description of sorting
    based on selected options in the sorting queue.
  */
  let sq = window.sorting_queue;
  var s_option_p = document.getElementById("selected_options");
  var hd = [];
  for (let condition of sq) {
    if (condition.selected)
      hd.push(
        `${condition.name}${condition.direction === "dsc" ? " descending" : ""}`
      );
  }
  s_option_p.innerHTML = hd.join(", ");
}

function modifySortingDirection(column) {
  /*
    Every time a radio button is clicked,
    the corresponding value in the sorting queue is modified.
  */
  let sq = window.sorting_queue;
  var s_option = sq.find((condition) => condition["name"] === column);
  if (!s_option) return;
  var type = document.querySelector(
    `input[name="${column}_direction"]:checked`
  ).value;
  sq[
    sq.findIndex(function (e) {
      return e === s_option;
    })
  ].direction = type;
  displaySortingDescription();
}
