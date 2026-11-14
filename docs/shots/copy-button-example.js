document.addEventListener("datasette_init", () => {
    const section = document.createElement("div");
    section.id = "my-plugin-copy";
    section.innerHTML = `
      <p>
        Install this plugin:
        <code id="my-plugin-command">datasette install my-plugin</code>
        <datasette-copy-button for="my-plugin-command" name="install command">
        </datasette-copy-button>
      </p>
      <p>
        Link to this page
        <datasette-copy-button name="link"></datasette-copy-button>
      </p>`;
    // The text to copy can also be set from JavaScript:
    section.querySelector('[name="link"]').value = location.href;
    document.querySelector("section.content").append(section);
});
