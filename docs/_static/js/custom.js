jQuery(function ($) {
  // Show banner linking to /stable/ if this is a /latest/ page
  if (!/\/latest\//.test(location.pathname)) {
    return;
  }
  var stableUrl = location.pathname.replace("/latest/", "/stable/");
  // Check it's not a 404
  fetch(stableUrl, { method: "HEAD" }).then((response) => {
    if (response.status == 200) {
      var warning = $(
        `<div class="admonition warning">
           <p class="first admonition-title">Note</p>
           <p class="last">
             This documentation covers the <strong>development version</strong> of Datasette.</p>
             <p>See <a href="${stableUrl}">this page</a> for the current stable release.
           </p>
        </div>`
      );
      warning.find("a").attr("href", stableUrl);
      $("article[role=main]").prepend(warning);
    }
  });
});
