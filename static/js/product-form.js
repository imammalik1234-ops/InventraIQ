const categorySelect = document.getElementById("category");
const productSelect = document.getElementById("product");
const variantSelect = document.getElementById("variant");
const supplierSelect = document.getElementById("supplier");
const productForm = document.getElementById("productForm");

function setOptions(select, values, placeholder, selectedValue) {
  select.innerHTML = `<option value="">${placeholder}</option>`;

  values.forEach(value => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === selectedValue;
    select.appendChild(option);
  });
}

function selectedProductInfo() {
  const category = categorySelect.value;
  const product = productSelect.value;

  if (!category || !product || !catalog[category] || !catalog[category][product]) {
    return null;
  }

  return catalog[category][product];
}

function populateProducts() {
  const category = categorySelect.value;
  const products = category && catalog[category] ? Object.keys(catalog[category]) : [];

  setOptions(productSelect, products, "Select a product", selectedValues.product);
  populateProductDetails();
}

function populateProductDetails() {
  const info = selectedProductInfo();

  setOptions(
    variantSelect,
    info ? info.variants : [],
    "Select a variant",
    selectedValues.variant
  );

  setOptions(
    supplierSelect,
    info ? info.suppliers : [],
    "Select a brand",
    selectedValues.supplier
  );
}

function showValidationErrors(errors) {
  let summary = document.querySelector(".validation-summary");

  if (!summary) {
    summary = document.createElement("div");
    summary.className = "notice danger validation-summary";
    productForm.parentElement.insertBefore(summary, productForm);
  }

  if (!summary.id) summary.id = "product-validation-summary";
  summary.setAttribute("role", "alert");
  summary.setAttribute("tabindex", "-1");
  const title = document.createElement("strong");
  title.textContent = "Please check your entries";
  const list = document.createElement("ul");
  errors.forEach(error => {
    const item = document.createElement("li");
    item.textContent = error;
    list.appendChild(item);
  });
  summary.replaceChildren(title, list);
  const descriptions = new Set((productForm.getAttribute("aria-describedby") || "")
    .split(/\s+/).filter(Boolean));
  descriptions.add(summary.id);
  productForm.setAttribute("aria-describedby", Array.from(descriptions).join(" "));
  summary.style.display = "block";
  summary.focus({ preventScroll: true });
  summary.scrollIntoView({ block: "center" });
}

function validateForm(event) {
  const errors = [];
  const quantity = Number(document.getElementById("quantity").value);
  const reorderLevel = Number(document.getElementById("reorder_level").value);
  const expiryInput = document.getElementById("expiry_date");

  if (!categorySelect.value) errors.push("Please select a category.");
  if (!productSelect.value) errors.push("Please select a product.");
  if (!variantSelect.value) errors.push("Please select a product variant.");
  if (!supplierSelect.value) errors.push("Please select a brand.");
  if (!Number.isInteger(quantity) || quantity < 0) errors.push("Quantity must be greater than or equal to 0.");
  if (!Number.isInteger(reorderLevel) || reorderLevel < 0) errors.push("Reorder level must be greater than or equal to 0.");
  if (!expiryInput.value) errors.push("Please select an expiry date.");

  if (expiryInput.min && expiryInput.value && expiryInput.value < expiryInput.min) {
    errors.push("Expiry date must be a future date after today.");
  }

  if (errors.length > 0) {
    event.preventDefault();
    // Trigger the shared, inline native-validation descriptions without sending a request.
    productForm.checkValidity();
    showValidationErrors(errors);
  }
}

categorySelect.addEventListener("change", () => {
  selectedValues.product = "";
  selectedValues.variant = "";
  selectedValues.supplier = "";
  populateProducts();
});

productSelect.addEventListener("change", () => {
  selectedValues.variant = "";
  selectedValues.supplier = "";
  populateProductDetails();
});

productForm.addEventListener("submit", validateForm);
populateProducts();
