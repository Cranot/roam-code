/* Copy-to-clipboard for install blocks */
document.querySelectorAll(".install-block").forEach(function (block) {
  block.addEventListener("click", function () {
    var text = block.getAttribute("data-copy") || block.textContent.trim();
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(function () {
      block.classList.add("copied");
      var label = block.querySelector(".copy-label");
      if (label) label.textContent = "copied!";
      setTimeout(function () {
        block.classList.remove("copied");
        if (label) label.textContent = "copy";
      }, 1500);
    });
  });
});
