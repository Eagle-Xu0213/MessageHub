const locationURL = '/api/workbench/locations';
const locationQuery = $('#location-query');
const locationAdd = $('#add-location');
const locationClear = $('#clear-locations');
const locationForm = $('#location-form');
const locationStatus = $('#location-status');
const locationSubmit = locationForm.querySelector('[type="submit"]');
const locationCancel = $('#location-cancel');
let availableLocations = [];
let selectedLocations = [];
let locationLoad = 0;
let locationsChanged = false;
let locationRevision;
let renamingLocation = null;

const cleanLocation = name => name.normalize('NFKC').trim().replace(/\s+/g, ' ');
const locationKey = name => cleanLocation(name).toLowerCase();

function locationButton(label, click, className = 'btn') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.textContent = label;
  button.onclick = click;
  return button;
}

function renderLocationPicker() {
  const query = locationKey(locationQuery.value);
  const chosenKeys = new Set(selectedLocations.map(place => locationKey(place.name)));
  $('#selected-locations').replaceChildren(...selectedLocations.map(place => {
    const button = locationButton(place.name + ' ×', () => {
      selectedLocations = selectedLocations.filter(selected => selected !== place);
      locationsChanged = true;
      renderLocationPicker();
      locationQuery.focus();
    }, 'badge location-remove');
    button.setAttribute('aria-label', '移除地点 ' + place.name);
    return button;
  }));
  locationClear.hidden = !selectedLocations.length;
  const unselected = availableLocations.filter(place => !chosenKeys.has(locationKey(place.name)));
  const matches = unselected.filter(place => locationKey(place.name).includes(query));
  $('#location-options').replaceChildren(...matches.map(place =>
    locationButton(place.name, () => selectEditorLocation(place))));
  locationAdd.hidden = !query || chosenKeys.has(query) ||
    availableLocations.some(place => locationKey(place.name) === query);
}

function selectEditorLocation(place) {
  selectedLocations.push(place);
  locationsChanged = true;
  locationQuery.value = '';
  renderLocationPicker();
  locationQuery.focus();
}

function resetEditorLocations(item) {
  const request = ++locationLoad;
  selectedLocations = [...(item?.locations || []), ...(item?.new_location_names || []).map(name => ({name}))];
  locationRevision = item?.updated_at;
  locationsChanged = false;
  availableLocations = [];
  locationQuery.value = '';
  const status = $('#location-picker-status');
  status.hidden = true;
  renderLocationPicker();
  categoryRequest(locationURL).then(({locations}) => {
    if (request !== locationLoad) return;
    availableLocations = locations;
    const byName = new Map(locations.map(place => [locationKey(place.name), place]));
    selectedLocations = selectedLocations.map(place => place.id ? place : byName.get(locationKey(place.name)) || place);
    renderLocationPicker();
  }).catch(error => {
    if (request !== locationLoad) return;
    status.textContent = '历史地点读取失败。' + error.message;
    status.hidden = false;
  });
}

function editorLocationPayload() {
  if (editing && !locationsChanged) return {};
  if (!editing && !selectedLocations.length) return {};
  return {
    location_ids: selectedLocations.filter(place => place.id).map(place => place.id),
    new_location_names: selectedLocations.filter(place => !place.id).map(place => place.name),
    ...(editing ? {expected_updated_at: locationRevision} : {}),
  };
}

locationQuery.oninput = renderLocationPicker;
locationQuery.onkeydown = event => {
  if (event.key !== 'Enter' || event.isComposing) return;
  event.preventDefault();
  if (!locationAdd.hidden) locationAdd.click();
};
locationAdd.onclick = () => selectEditorLocation({name: cleanLocation(locationQuery.value)});
locationClear.onclick = () => {
  selectedLocations = [];
  locationsChanged = true;
  renderLocationPicker();
  locationQuery.focus();
};

function resetLocationForm() {
  renamingLocation = null;
  locationForm.reset();
  locationSubmit.textContent = '添加地点';
  locationCancel.hidden = true;
}

async function showLocations() {
  try {
    const {locations} = await categoryRequest(locationURL);
    const list = $('#location-list');
    list.replaceChildren(...locations.map(place => {
      const item = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = place.name + ' · ' + place.task_count + ' 项';
      const rename = locationButton('改名', () => {
        renamingLocation = place.id;
        locationForm.elements.name.value = place.name;
        locationSubmit.textContent = '保存名称';
        locationCancel.hidden = false;
        locationForm.elements.name.focus();
      });
      const remove = locationButton('删除', async () => {
        if (!window.confirm('删除「' + place.name + '」并从 ' + place.task_count + ' 项待办移除？')) return;
        remove.disabled = true;
        try {
          const {updated} = await categoryRequest(locationURL + '/' + place.id, 'DELETE');
          if (renamingLocation === place.id) resetLocationForm();
          locationStatus.textContent = '已删除地点，已从 ' + updated + ' 项待办移除。';
          notifyDshTasks();
          await Promise.all([showLocations(), load()]);
        } catch (error) {
          locationStatus.textContent = error.message;
        } finally {
          remove.disabled = false;
        }
      });
      item.append(name, rename, remove);
      return item;
    }));
  } catch (error) {
    locationStatus.textContent = error.message;
  }
}

locationForm.onsubmit = async event => {
  event.preventDefault();
  const id = renamingLocation;
  locationSubmit.disabled = true;
  try {
    await categoryRequest(id ? locationURL + '/' + id : locationURL, id ? 'PUT' : 'POST',
      {name: locationForm.elements.name.value});
    resetLocationForm();
    locationStatus.textContent = id ? '已更新地点名称。' : '地点已保存，可在待办中复用。';
    notifyDshTasks();
    await Promise.all([showLocations(), load()]);
  } catch (error) {
    locationStatus.textContent = error.message;
  } finally {
    locationSubmit.disabled = false;
  }
};
locationCancel.onclick = resetLocationForm;
