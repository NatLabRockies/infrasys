# How to save the system

## Exporting the entire contents of the system

If the user wants to create a folder that contains all the information of the
system `infrasys` provides a simple method `system.save("my_folder")` that
creates a folder (if it does not exist) and save all the contents of the
system including the `system.to_json()` and the time series arrow files and
sqlite.

To archive the system into a zip file, the user can use `system.save("my_folder",
zip=True)`. This will create a zip folder of the contents of `my_folder` and
delete the folder once the archive is done.
