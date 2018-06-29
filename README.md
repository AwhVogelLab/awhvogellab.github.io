# awhvogellab.github.io

This website is powered by github pages and jekyll. Github pages hosts the server that allows people to access our content when they go to our URL. Jekyll allows us to generate pages using markdown and gives us a basic theme to work with. 

## Basics

### Adding a page

If you want to add a page, you just have to make a new markdown (.md) file. You can do this with any text editor (except word), just save it as `.md` instead of `.txt`. Jekyll will automatically parse it and turn it into html. The page is then immediately available at `/filename`. See the `example.md` file to check out some basic syntax and `/example` to see how it looks. Just make sure the header that specifies the layout as default is present in your new page. This tells jekyll what css to use.

### Adding content

If you want to add a photo or a paper to an existing page, just copy the syntax that is present. Make sure you place your file in the appropriate folder. Commit your changes and you are done!

### Committing changes

Whenever you want to make a change, you will have to commit changes in order for them to go live. To this, you will need push access on the lab organization. Talk to someone who has it already to get this set up. Then run the following command to get a copy of the files in your working directory:

`git clone https://github.com/AwhVogelLab/awhvogellab.github.io.git`

If you don’t have git installed, you’ll get a message telling you to install it. Once you have the files, make whatever changes you want to make. Then run the following commands:

`git status`

Check to make sure only files you actually changed show up.

`git add -A`

This "stages" all your changes.

`git commit -m 'a helpful message about what you did'`

This commits your changes to your local history, but we still need to send them to github

`git push`

This may require you to provide your github username and password

That’s it! Your changes are now live. You can now safely delete that folder.

## Resources

git basics:

http://rogerdudler.github.io/git-guide/

markdown syntax:

https://daringfireball.net/projects/markdown/syntax

Github pages and Jekyll info:

https://help.github.com/articles/using-jekyll-as-a-static-site-generator-with-github-pages/

HTML basics:

https://www.w3schools.com/html/html_basic.asp

Jekyll docs:

https://jekyllrb.com/docs/home/

## Advanced

If you want to make bigger changes in layout or style, you are free to edit the `_layouts/default.html` file which holds things like the navigation bar or `assets/css/style.css` which holds custom styling. It is not hard to do, but please use `class`es and `id`s so that your changes don’t have unintended side effects. You can also make your own layouts and styles. Just stick them in the same folders, link your css file in the html file, and reference them in the markdown file header.